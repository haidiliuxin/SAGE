"""自适应切片测试：keyspace 切片纯函数、适配层 -s/-l、执行层渐进切片。

背景（100 条评测的结论）：原生攻击（词表 × 规则链 / 掩码 / 词表 × 掩码）此前一次
把整段键空间拉完，调度器每轮只有一个决策点；最终轮 332 个决策全是探索、利用阶段 0 次，
65 个命中里 51 个来自首个批次。本模块覆盖"把原生单元切成多个可观测切片"的实现：

- `keyspace.split_mask()`：掩码不支持 hashcat 的 -s/-l（基/模语义不可预测），只能按
  可预测的"固定首个可拆占位符"拆成子掩码；
- `keyspace.slice_words()`：`-a 0/6/7` 用 `-s a -l b` 切片，语义是词表第 a..b 条；
- 适配层把切片写进 argv；
- 执行层按键空间推进"探针 → 倍增"的切片，并保持
  `tested ≤ candidate_count ≤ 该批次分配预算` 不变量。
"""

from __future__ import annotations

from pathlib import Path

from sage_pass.keyspace import masks_keyspace, slice_words, split_mask


# --------------------------------------------------------------- 掩码拆分
def test_split_mask_by_first_placeholder():
    slices = split_mask("?d?d?d?d", max_keys=1_000)

    assert len(slices) == 10
    assert slices[0] == "0?d?d?d"
    assert slices[-1] == "9?d?d?d"
    assert all(masks_keyspace([mask]) == 1_000 for mask in slices)


def test_split_mask_recurses_until_each_slice_fits():
    slices = split_mask("?l?l?l", max_keys=100)

    # ?l?l?l = 17576；拆首个 ?l 后每片 676，仍超 100 → 继续拆，得到 26×26 = 676 片
    assert len(slices) == 676
    assert all(masks_keyspace([mask]) == 26 for mask in slices)


def test_split_mask_keeps_literal_prefix():
    slices = split_mask("abc?d", max_keys=5)

    assert slices == [f"abc{digit}" for digit in "0123456789"]


def test_split_mask_respects_custom_charset():
    slices = split_mask("?1?1", max_keys=3, custom_charsets=("abc",))

    assert slices == ["a?1", "b?1", "c?1"]


def test_split_mask_returns_single_slice_when_not_splittable():
    # 没有占位符、已经够小、或占位符未提供自定义字符集 → 原样返回
    assert split_mask("plain", max_keys=1) == ["plain"]
    assert split_mask("?d", max_keys=100) == ["?d"]
    assert split_mask("?1?1", max_keys=3) == ["?1?1"]


def test_split_mask_caps_slice_count():
    slices = split_mask("?d?d?d?d", max_keys=10, max_slices=64)

    assert len(slices) <= 64
    # 封顶时允许仍有超大片，但不能丢键空间
    assert sum(masks_keyspace([mask]) for mask in slices) == 10_000


# --------------------------------------------------------------- 词区间切片
def test_slice_words_counts_within_remaining():
    # 81 词、规则链放大 100 倍、目标 250 键 → 每片 2 词
    assert slice_words(total_words=81, offset=0, factor=100, target_keys=250) == 2
    assert slice_words(total_words=81, offset=80, factor=100, target_keys=250) == 1
    assert slice_words(total_words=81, offset=81, factor=100, target_keys=250) == 0


def test_slice_words_always_at_least_one_word():
    # 目标键空间小于单个词的放大倍数时，仍要推进一片（否则调度器会原地打转）
    assert slice_words(total_words=5, offset=0, factor=1000, target_keys=10) == 1
    assert slice_words(total_words=5, offset=0, factor=1000, target_keys=0) == 1


# --------------------------------------------------------------- 适配层 argv
def test_native_wordlist_slice_emits_skip_and_limit(tmp_path):
    import sys

    from sage_pass.hashcat_adapter import HashcatAdapter, HashcatJob

    log_path = tmp_path / "argv.jsonl"
    script = (
        "import json,sys; "
        f"open({str(log_path)!r},'a',encoding='utf-8').write(json.dumps(sys.argv[1:])+'\\n'); "
        "print('{\"progress\":[1,1]}'); raise SystemExit(1)"
    )
    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"w{i}" for i in range(10)) + "\n", encoding="utf-8")

    adapter = HashcatAdapter((sys.executable, "-c", script))
    adapter.start(
        HashcatJob(
            run_id="R-SLICE",
            target_hashes=("hash",),
            hash_mode=0,
            attack_mode=0,
            wordlist_path=wordlist,
            wordlist_skip=4,
            wordlist_limit=6,
            timeout_seconds=10,
        )
    ).wait()

    import json

    argv = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert argv[argv.index("-s") + 1] == "4"
    assert argv[argv.index("-l") + 1] == "6"


def test_wordlist_skip_rejected_without_wordlist(tmp_path):
    import sys

    import pytest

    from sage_pass.errors import AppError
    from sage_pass.hashcat_adapter import HashcatAdapter, HashcatJob

    adapter = HashcatAdapter((sys.executable, "-c", "raise SystemExit(1)"))
    with pytest.raises(AppError):
        adapter.start(
            HashcatJob(
                run_id="R-BAD",
                target_hashes=("hash",),
                hash_mode=0,
                attack_mode=0,
                candidates=("alpha",),
                wordlist_limit=2,
                timeout_seconds=10,
            )
        )


def test_wordlist_skip_requires_limit_after_skip(tmp_path):
    import pytest

    from sage_pass.errors import AppError
    from sage_pass.hashcat_adapter import HashcatAdapter, HashcatJob

    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("alpha\nbeta\n", encoding="utf-8")
    adapter = HashcatAdapter(("hashcat-not-installed-xyz",))

    with pytest.raises(AppError):
        adapter.start(
            HashcatJob(
                run_id="R-BAD",
                target_hashes=("hash",),
                hash_mode=0,
                attack_mode=0,
                wordlist_path=wordlist,
                wordlist_skip=5,
                wordlist_limit=5,
                timeout_seconds=10,
            )
        )
    with pytest.raises(AppError):
        adapter.start(
            HashcatJob(
                run_id="R-BAD",
                target_hashes=("hash",),
                hash_mode=0,
                attack_mode=0,
                wordlist_path=wordlist,
                wordlist_skip=1,
                timeout_seconds=10,
            )
        )

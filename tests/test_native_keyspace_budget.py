"""原生攻击键空间估算与预算适配测试。

背景：调度层要求 `tested ≤ candidate_count ≤ 该单元的分配预算`，而原生攻击
（掩码 / 词表 × 规则 / 词表 × 掩码）的候选由 hashcat 自己枚举。这里覆盖键空间
估算、计划层预算分配、执行层裁剪（-l / 掩码裁剪 / 越界跳过）三处逻辑。
"""

from __future__ import annotations

from sage_pass.hashcat_adapter import HashcatJob
from sage_pass.keyspace import (
    file_line_count,
    mask_keyspace,
    masks_keyspace,
    native_keyspace,
    rule_chain_count,
    rule_line_count,
)
from sage_pass.planner import NativeAttackSettings, RulePlanner
from sage_pass.real_executor import _fit_job_to_budget
from sage_pass.schemas import PRIR


def test_mask_keyspace_builtin_charsets():
    assert mask_keyspace("?d?d?d?d") == 10_000
    assert mask_keyspace("?l?l") == 676
    assert mask_keyspace("?u?l?l?l?l?d?d") == 26 * 26**4 * 100
    assert mask_keyspace("prefix?d") == 10
    # ?? 是字面量 ?，不放大空间
    assert mask_keyspace("??d") == 1


def test_mask_keyspace_custom_charset_and_unknown_token():
    assert mask_keyspace("?1?1", custom_charsets=["abc"]) == 9
    # 未提供自定义字符集 → 无法估算
    assert mask_keyspace("?1?1") is None
    assert masks_keyspace(["?d?d", "?d?d?d?d"]) == 10_000 + 100


def test_file_and_rule_line_counts(tmp_path):
    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    rules = tmp_path / "best66.rule"
    rules.write_text("$1\n$2\nc\n", encoding="utf-8")

    assert file_line_count(wordlist) == 3
    assert file_line_count(tmp_path / "missing.txt") == 0
    assert rule_line_count([rules], inline_rules=[":", "$9"]) == 5


def test_native_keyspace_shapes():
    # -a 0 词表 × 规则
    assert (
        native_keyspace(
            attack_mode=0, wordlist_lines=81, rule_files=["best66.rule"]
        )
        is not None
    )
    # -a 6 词表 × 掩码
    assert native_keyspace(attack_mode=6, wordlist_lines=81, masks=["?d?d"]) == 8_100
    # -a 3 纯掩码
    assert native_keyspace(attack_mode=3, masks=["?d?d?d?d"]) == 10_000


def _prir(candidate_budget: int = 100_000) -> PRIR:
    return PRIR(
        task_id="T-KEYSPACE",
        target_type="hash",
        algorithm="md5",
        salt=False,
        verification_cost="low",
        context_available=False,
        candidate_space=1_000_000,
        time_budget=300,
        candidate_budget=candidate_budget,
        status="analyzed",
        confidence=0.8,
        warnings=[],
        information_profile=None,
    )


def test_planner_sizes_s1_budget_by_wordlist_times_rules(tmp_path):
    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"word{index}" for index in range(100)) + "\n", encoding="utf-8")
    rules = tmp_path / "best66.rule"
    rules.write_text("\n".join(f"$%d" % index for index in range(10)), encoding="utf-8")

    plan = RulePlanner(
        native_attacks=NativeAttackSettings(
            rule_paths=(str(rules),),
            wordlist_path=str(wordlist),
            mask_ladder=("?d?d?d?d",),
        )
    ).plan(_prir())
    by_id = {item.strategy_id.value: item for item in plan.strategies}

    # 键空间 = 100 词 × 10 规则 = 1000；预算取"权重分配额与键空间的较大者"，
    # 但不小于键空间（保证实测候选数不会超出该单元的分配预算）。
    assert by_id["S1"].candidate_budget >= 1_000
    assert by_id["S1"].parameters["hashcat_rule_files"] == [str(rules)]
    # 掩码单元同样按键空间分配（10000 ≤ 可用预算）
    assert by_id["S6"].candidate_budget >= 10_000


def test_budget_squeeze_keeps_cheap_high_value_hybrid_year_mask(tmp_path):
    """预算不够时先砍"纯掩码"里最贵的，而不是砍"词 + 年份"这类高价值混合掩码。

    真实故障：任务候选预算 100 万时，计划层先把 S7 的 `?d?d?d?d`（词 + 4 位年份）裁掉，
    留下 S6 的 `?l?l?l?l`（随机字母，几乎无实战价值），导致 `summer2023` 这类口令
    在候选空间里根本不存在。
    """
    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"word{index}" for index in range(81)) + "\n", encoding="utf-8")

    plan = RulePlanner(
        native_attacks=NativeAttackSettings(
            wordlist_path=str(wordlist),
            mask_ladder=("?d?d?d?d", "?l?l?l?l", "?l?l?l?l?d?d"),
            hybrid_masks=("?d?d?d?d", "?d?d", "!"),
        )
    ).plan(_prir(candidate_budget=1_000_000))
    by_id = {item.strategy_id.value: item for item in plan.strategies}

    assert by_id["S7"].parameters["hashcat_hybrid_mask"] == ["?d?d?d?d", "?d?d", "!"]
    # 被牺牲的是纯掩码单元里最贵的那个（?l?l?l?l = 456976）
    assert "?l?l?l?l" not in by_id["S6"].parameters["hashcat_masks"]
    # 裁剪必须留下可执行的提示（说明需要的预算）
    assert any("候选预算" in warning for warning in plan.warnings), plan.warnings


def test_warning_states_required_budget_when_native_trimmed(tmp_path):
    """裁剪警告必须写清"需要多少候选预算"，否则用户无法判断该调多大。"""
    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"word{index}" for index in range(81)) + "\n", encoding="utf-8")
    rules = tmp_path / "two.rule"
    rules.write_text("$1\n$2\n", encoding="utf-8")  # 每词 2 条规则 → 键空间 162

    plan = RulePlanner(
        native_attacks=NativeAttackSettings(
            rule_paths=(str(rules),),
            wordlist_path=str(wordlist),
        )
    ).plan(_prir(candidate_budget=100))
    warnings = " ".join(plan.warnings)

    assert "162" in warnings, plan.warnings
    assert "候选预算" in warnings, plan.warnings


def test_planner_trims_hybrid_masks_that_do_not_fit(tmp_path):
    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("alpha\nbeta\n", encoding="utf-8")
    plan = RulePlanner(
        native_attacks=NativeAttackSettings(
            wordlist_path=str(wordlist),
            hybrid_masks=("?d?d", "?d?d?d?d"),
        )
    ).plan(_prir(candidate_budget=5_000))
    by_id = {item.strategy_id.value: item for item in plan.strategies}
    # 2 词 × ?d?d?d?d = 20000 装不进预算，只保留 ?d?d（2 × 100 = 200）
    assert by_id["S7"].parameters["hashcat_hybrid_mask"] == ["?d?d"]
    assert by_id["S7"].candidate_budget >= 200


def _job(**overrides) -> HashcatJob:
    values = {
        "run_id": "R-KEYSPACE",
        "target_hashes": ("hash",),
        "hash_mode": 0,
    }
    values.update(overrides)
    return HashcatJob(**values)


def test_fit_job_truncates_wordlist_with_limit(tmp_path):
    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"word{index}" for index in range(100)) + "\n", encoding="utf-8")
    rules = tmp_path / "best66.rule"
    rules.write_text("\n".join(f"$%d" % index for index in range(10)), encoding="utf-8")

    job = _job(wordlist_path=wordlist, attack_mode=0, rule_files=(str(rules),))
    fitted, reason = _fit_job_to_budget(job, allocation=250)
    assert reason == ""
    # 250 // 10 规则 = 25 条词表 → 实测 250 条，正好等于分配预算
    assert fitted is not None and fitted.wordlist_limit == 25

    small, reason = _fit_job_to_budget(job, allocation=5_000)
    assert reason == "" and small is not None and small.wordlist_limit is None


def test_fit_job_trims_masks_and_skips_hopeless_unit():
    job = _job(attack_mode=3, masks=("?d?d", "?d?d?d?d", "?l?l?l?l"))
    fitted, reason = _fit_job_to_budget(job, allocation=10_000)
    assert reason == ""
    # 预算 10000：优先保留能装下的最大掩码，尽量把预算花满
    assert fitted is not None and fitted.masks == ("?d?d?d?d",)

    skipped, reason = _fit_job_to_budget(job, allocation=10)
    assert skipped is None and "超出本批次候选预算" in reason


def test_fit_job_truncates_hybrid_wordlist(tmp_path):
    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"word{index}" for index in range(50)) + "\n", encoding="utf-8")
    job = _job(attack_mode=6, wordlist_path=wordlist, masks=("?d?d",))
    fitted, reason = _fit_job_to_budget(job, allocation=1_000)
    assert reason == ""
    # 50 词 × 100 = 5000 > 1000 → 只读 10 条词
    assert fitted is not None and fitted.wordlist_limit == 10


def test_rule_chain_multiplies_multiple_rule_files(tmp_path):
    """多个 `-r` 是规则链（乘积），不是并集——hashcat 会打印 Rules: a×b。"""
    first = tmp_path / "a.rule"
    first.write_text("$1\n$2\n$3\n", encoding="utf-8")
    second = tmp_path / "b.rule"
    second.write_text("c\nso0\n", encoding="utf-8")

    assert rule_line_count([first, second]) == 5
    assert rule_chain_count([first, second]) == 6
    assert rule_chain_count([first]) == 3
    # 键空间 = 词表条数 × 规则链倍数
    assert (
        native_keyspace(
            attack_mode=0, wordlist_lines=10, rule_files=[first, second]
        )
        == 60
    )


def test_fit_job_uses_rule_chain_factor(tmp_path):
    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"word{index}" for index in range(10)) + "\n", encoding="utf-8")
    first = tmp_path / "a.rule"
    first.write_text("\n".join("$1" for _ in range(5)), encoding="utf-8")
    second = tmp_path / "b.rule"
    second.write_text("\n".join("c" for _ in range(4)), encoding="utf-8")

    job = _job(
        attack_mode=0,
        wordlist_path=wordlist,
        rule_files=(str(first), str(second)),
    )
    # 10 词 × 5 × 4 = 200 键；预算 40 → 只能读 2 条词
    fitted, reason = _fit_job_to_budget(job, allocation=40)
    assert reason == ""
    assert fitted is not None and fitted.wordlist_limit == 2
    # 没有外部词表时（Python 候选 + 掩码）只能裁剪候选列表，不能用 -l。
    job = _job(
        attack_mode=6,
        candidates=tuple(f"word{index}" for index in range(50)),
        masks=("?d?d",),
    )
    fitted, reason = _fit_job_to_budget(job, allocation=1_000)

    assert reason == ""
    assert fitted is not None
    assert len(fitted.candidates) == 10
    assert fitted.wordlist_limit is None


def _progress_adapter(tmp_path, progress: int, name: str):
    """假 hashcat：只打印一条 status-json，供解析/记账测试使用。"""
    import sys

    from sage_pass.hashcat_adapter import HashcatAdapter

    script = (
        "import sys; "
        f"print('{{\"progress\":[{progress},{progress}]}}'); "
        "raise SystemExit(1)"
    )
    return HashcatAdapter((sys.executable, "-c", script))


def test_mask_job_accounts_keyspace_not_mask_count(tmp_path):
    """掩码作业的候选量是键空间，而不是"掩码个数"（曾把实测条数夹成 2）。"""
    adapter = _progress_adapter(tmp_path, 10_676, "mask")
    result = adapter.start(
        _job(attack_mode=3, masks=("?d?d?d?d", "?l?l"), timeout_seconds=10)
    ).wait()

    assert result.tested == 10_676


def test_wordlist_rule_job_accounts_keyspace(tmp_path):
    """词表 × 规则的候选量是词表条数 × 规则条数，而不是估计值。"""
    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    rules = tmp_path / "best66.rule"
    rules.write_text("$1\n$2\n$3\n$4\n$5\n", encoding="utf-8")

    adapter = _progress_adapter(tmp_path, 15, "rules")
    result = adapter.start(
        _job(
            attack_mode=0,
            wordlist_path=wordlist,
            rule_files=(str(rules),),
            candidate_estimate=3,
            timeout_seconds=10,
        )
    ).wait()

    assert result.tested == 15

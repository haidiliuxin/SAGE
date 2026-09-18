"""hashcat 原生攻击的键空间估算（掩码 / 词表 × 规则 / 词表 × 掩码）。

调度器按"实际测试的候选数"记账（`candidate_count` 与 `tested` 都不得超出该单元的
候选预算），而原生攻击的候选由 hashcat 自己枚举：掩码阶梯、词表 × 规则、词表 × 掩码
的真实测试量都远大于计划里的 Python 候选数。这里给出一个可预测的键空间模型：

- `-a 3`（纯掩码）：各掩码键空间之和；
- `-a 0`（词表/候选 + 规则）：基准条数 × 规则条数；
- `-a 6/7`（词表 + 掩码）：词表条数 × 各掩码键空间之和。

计划层用它给原生单元分配候选预算，执行层用它做启动前的兜底裁剪（配合 hashcat 的
`-l/--limit` 截断词表条数），从而保证"实测候选数 ≤ 分配预算"这一调度不变量。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

# hashcat 内置字符集大小（?s 为 33 个符号，含空格）。
MASK_CHARSETS: dict[str, int] = {
    "?l": 26,
    "?u": 26,
    "?d": 10,
    "?s": 33,
    "?a": 95,
    "?b": 256,
    "?h": 16,
    "?H": 16,
}

_LINE_COUNT_CACHE: dict[tuple[str, int, int], int] = {}


def is_mask_token(mask: str) -> bool:
    return mask.startswith("?")


def mask_keyspace(
    mask: str,
    *,
    custom_charsets: Sequence[str] = (),
) -> int | None:
    """单个掩码的键空间；含未知占位符（未提供的 `?1`..`?4`）时返回 None。"""
    text = mask.strip()
    if not text:
        return None
    total = 1
    index = 0
    while index < len(text):
        char = text[index]
        if char == "?" and index + 1 < len(text):
            token = text[index : index + 2]
            if token == "??":
                index += 2
                continue
            if token in MASK_CHARSETS:
                total *= MASK_CHARSETS[token]
                index += 2
                continue
            if token[1] in "1234":
                position = int(token[1]) - 1
                if position >= len(custom_charsets):
                    return None
                size = len({char for char in str(custom_charsets[position])})
                if size <= 0:
                    return None
                total *= size
                index += 2
                continue
            return None
        index += 1
    return total


def masks_keyspace(
    masks: Sequence[str],
    *,
    custom_charsets: Sequence[str] = (),
) -> int | None:
    """多个掩码的键空间之和（任一掩码无法估算即返回 None）。"""
    if not masks:
        return None
    total = 0
    for mask in masks:
        size = mask_keyspace(mask, custom_charsets=custom_charsets)
        if size is None:
            return None
        total += size
    return total


def select_masks_within(
    masks: Sequence[str],
    *,
    base: int,
    budget: int,
    custom_charsets: Sequence[str] = (),
) -> tuple[list[str], int]:
    """在候选预算内挑选掩码：优先大的（尽量把预算花满），返回 (保留的掩码, 键空间)。

    `base` 是每个掩码键空间前的放大基数（纯掩码为 1，混合攻击为词表条数）。
    无法估算键空间的掩码一律丢弃（保守：宁可不跑也不越预算）。
    """
    sized: list[tuple[int, int, str]] = []
    for index, mask in enumerate(masks):
        size = mask_keyspace(mask, custom_charsets=custom_charsets)
        if size is None:
            continue
        sized.append((size, index, mask))
    sized.sort(key=lambda item: (-item[0], item[1]))
    order = {mask: index for index, mask in enumerate(masks)}
    kept: list[str] = []
    used = 0
    for size, _, mask in sized:
        cost = size * max(1, base)
        if used + cost <= budget:
            kept.append(mask)
            used += cost
    kept.sort(key=lambda mask: order[mask])
    return kept, used


def file_line_count(path: str | Path) -> int:
    """文件行数（含空行，与 hashcat 的 "Counting lines" 语义对齐）。

    刻意取**上界**：估算偏大只会让我们更保守地裁剪键空间，绝不会让实测候选数
    超出分配预算（那是调度不变量的破坏方向）。文件不存在或不可读时返回 0。
    """
    try:
        resolved = Path(path).expanduser()
        stat = resolved.stat()
    except OSError:
        return 0
    key = (str(resolved), int(stat.st_size), int(stat.st_mtime_ns))
    cached = _LINE_COUNT_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with resolved.open("r", encoding="utf-8", errors="ignore") as handle:
            count = sum(1 for _ in handle)
    except OSError:
        return 0
    _LINE_COUNT_CACHE[key] = count
    return count


def rule_line_count(
    paths: Iterable[str | Path] = (),
    *,
    inline_rules: Iterable[str] = (),
) -> int:
    """规则条数之和：规则文件有效行数 + 内联规则条数（非空行）。

    注意：hashcat 对多个 `-r` 文件做的是**规则链**（先按第 1 个文件的规则变换，
    再按第 2 个继续变换），键空间是**乘积**——见 `rule_chain_count()`。
    """
    total = sum(file_line_count(path) for path in paths)
    total += sum(1 for rule in inline_rules if str(rule).strip())
    return total


def rule_chain_count(
    paths: Iterable[str | Path] = (),
    *,
    inline_rules: Iterable[str] = (),
) -> int:
    """规则链的放大倍数：多个规则文件相乘（hashcat 的 `-r a -r b` = 先 a 后 b）。

    实测（hashcat 7.1.2）：`-r best66.rule -r dive.rule` 会打印
    `Rules: 6512220` = 66 × 98670，键空间 = 词表条数 × 6512220。
    单个文件时等价于该文件的规则条数。
    """
    factors = [
        max(1, file_line_count(path))
        for path in paths
        if file_line_count(path) > 0
    ]
    inline = sum(1 for rule in inline_rules if str(rule).strip())
    if inline:
        factors.append(inline)
    if not factors:
        return 1
    total = 1
    for factor in factors:
        total *= factor
    return total


def wordlist_factor(
    *,
    wordlist_lines: int,
    masks: Sequence[str] = (),
    custom_charsets: Sequence[str] = (),
    rule_files: Iterable[str | Path] = (),
    inline_rules: Iterable[str] = (),
    attack_mode: int,
) -> int | None:
    """每个词表条目被放大的倍数（规则链乘积或掩码键空间）。"""
    if attack_mode == 3:
        return None
    if attack_mode in {6, 7}:
        return masks_keyspace(masks, custom_charsets=custom_charsets)
    return rule_chain_count(rule_files, inline_rules=inline_rules)


def native_keyspace(
    *,
    attack_mode: int,
    wordlist_lines: int = 0,
    candidate_count: int = 0,
    masks: Sequence[str] = (),
    custom_charsets: Sequence[str] = (),
    rule_files: Iterable[str | Path] = (),
    inline_rules: Iterable[str] = (),
) -> int | None:
    """原生攻击一次批次会测试的候选条数；无法确定时返回 None。"""
    if attack_mode == 3:
        return masks_keyspace(masks, custom_charsets=custom_charsets)
    base = wordlist_lines if wordlist_lines > 0 else candidate_count
    if base <= 0:
        return None
    factor = wordlist_factor(
        wordlist_lines=base,
        masks=masks,
        custom_charsets=custom_charsets,
        rule_files=rule_files,
        inline_rules=inline_rules,
        attack_mode=attack_mode,
    )
    if factor is None:
        return None
    return base * factor

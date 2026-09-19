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

# 可枚举的字符集内容（?b 为 0x00-0xff，不能安全地用文本表示，因此不参与拆分）。
MASK_CHARSET_CHARS: dict[str, str] = {
    "?l": "abcdefghijklmnopqrstuvwxyz",
    "?u": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "?d": "0123456789",
    "?h": "0123456789abcdef",
    "?H": "0123456789ABCDEF",
    "?s": " !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
}
MASK_CHARSET_CHARS["?a"] = (
    MASK_CHARSET_CHARS["?l"] + MASK_CHARSET_CHARS["?u"]
    + MASK_CHARSET_CHARS["?d"] + MASK_CHARSET_CHARS["?s"]
)

MAX_MASK_SLICES = 4096


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


def _mask_tokens(
    mask: str,
    custom_charsets: Sequence[str] = (),
) -> list[tuple[int, int, str | None]]:
    """掩码里的占位符：[(起始下标, 长度, 字符集内容)]。

    字符集内容为 None 表示无法枚举（未知占位符、`?b`、或未提供的 `?1..?4`），
    此时该占位符不能用来拆掩码。
    """
    tokens: list[tuple[int, int, str | None]] = []
    index = 0
    while index < len(mask):
        if mask[index] == "?" and index + 1 < len(mask):
            token = mask[index : index + 2]
            if token != "??":
                if token in MASK_CHARSET_CHARS:
                    chars: str | None = MASK_CHARSET_CHARS[token]
                elif token[1] in "1234":
                    position = int(token[1]) - 1
                    chars = (
                        "".join(dict.fromkeys(str(custom_charsets[position])))
                        if position < len(custom_charsets)
                        else None
                    )
                else:
                    chars = None
                tokens.append((index, 2, chars))
                index += 2
                continue
            index += 2
            continue
        index += 1
    return tokens


def split_mask(
    mask: str,
    *,
    max_keys: int,
    custom_charsets: Sequence[str] = (),
    max_slices: int = MAX_MASK_SLICES,
) -> list[str]:
    """把掩码拆成若干子掩码，使每个子掩码的键空间尽量不超过 `max_keys`。

    hashcat 的 `-s/-l` 对掩码是 base/mod 语义（实测 `?d?d?d?d -s 0 -l 100` 连第一个
    候选都没测到），因此掩码切片只能靠"固定首个可拆占位符"：`?d?d?d?d` →
    `0?d?d?d`、`1?d?d?d` …，每个子掩码的键空间精确可预测，需要时递归拆分。

    无法拆分时（没有占位符、占位符字符集为 1、或占位符未提供自定义字符集）原样返回；
    切片数超过 `max_slices` 时停止拆分（保留更大但完整的切片，绝不丢键空间）。
    """
    text = mask.strip()
    keyspace = mask_keyspace(mask, custom_charsets=custom_charsets)
    if keyspace is None or keyspace <= max_keys:
        return [text] if text else []

    current = [text]
    for _ in range(8):  # 掩码最长为 256 位，实际不会超过这个层数
        bigger = [
            item
            for item in current
            if (mask_keyspace(item, custom_charsets=custom_charsets) or 0) > max_keys
        ]
        if not bigger:
            break
        expanded: list[str] = []
        changed = False
        for item in current:
            size = mask_keyspace(item, custom_charsets=custom_charsets) or 0
            split_done = False
            if size > max_keys:
                for index, length, chars in _mask_tokens(item, custom_charsets):
                    if not chars or len(set(chars)) < 2:
                        continue
                    expanded.extend(
                        item[:index] + char + item[index + length :]
                        for char in dict.fromkeys(chars)
                    )
                    split_done = True
                    changed = True
                    break
            if not split_done:
                expanded.append(item)
        if not changed or len(expanded) > max_slices:
            break
        current = expanded
    return current


def slice_words(
    *,
    total_words: int,
    offset: int,
    factor: int,
    target_keys: int,
) -> int:
    """`-a 0/6/7` 切片：返回本片读取的词数（`-s offset -l offset+word_count`）。

    至少返回 1（除非词表已用尽），否则调度器会在同一个偏移上原地打转。
    """
    remaining = max(0, total_words - max(0, offset))
    if remaining == 0:
        return 0
    per_word = max(1, factor)
    words = max(1, int(target_keys) // per_word)
    return max(1, min(remaining, words))


def effective_wordlist_lines(
    path: str | Path,
    *,
    skip: int | None = 0,
    limit: int | None = None,
) -> int:
    """切片后实际读取的词表条数（hashcat `-s` 跳过、`-l` 为绝对条数）。"""
    total = file_line_count(path)
    start = max(0, int(skip or 0))
    if limit is None:
        return max(0, total - start)
    return max(0, min(total, int(limit)) - start)


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

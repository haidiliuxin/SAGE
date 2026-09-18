"""规则集 / 掩码对 100 条评测口令的覆盖实验（离线直接调 hashcat，用于挑规则集）。

用法（仓库根目录）：
    $env:PYTHONIOENCODING='utf-8'
    .\\venv\\Scripts\\python.exe scripts\\rules_probe.py

要点：
- 多个 `-r` 在 hashcat 里是**规则链**（规则数相乘），不是并集；把多份规则合并成
  一个文件才是并集。两种方式都会打印在结果里，用来对比覆盖与键空间。
- 规则链很吃主机内存，三份以上大规则文件容易报
  `Not enough allocatable memory (RAM) for this ruleset`。
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HASHCAT = Path(os.environ.get("SAGE_HASHCAT_PATH", r"F:\SA\tools\hashcat-7.1.2\hashcat.exe"))
RULES_DIR = HASHCAT.parent / "rules"
WORDLIST = REPO_ROOT / "data" / "benchmark" / "base-words.txt"
WORK = REPO_ROOT / "data" / "rules-probe"


def load_corpus() -> list[dict]:
    spec = importlib.util.spec_from_file_location(
        "bench", REPO_ROOT / "scripts" / "benchmark_100.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO_ROOT / "src"))
    spec.loader.exec_module(module)
    return module.corpus()


def run_hashcat(args: list[str], outfile: Path) -> int:
    outfile.unlink(missing_ok=True)
    command = [
        str(HASHCAT),
        "-m",
        "0",
        "--potfile-disable",
        "--outfile",
        str(outfile),
        "--outfile-format",
        "2",
        *args,
    ]
    completed = subprocess.run(
        command,
        cwd=str(HASHCAT.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    if completed.returncode not in (0, 1):
        print("  失败：", (completed.stderr or completed.stdout)[-300:])
        return -1
    if not outfile.is_file():
        return 0
    return len({line.strip() for line in outfile.read_text(encoding="utf-8").splitlines() if line.strip()})


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    cases = load_corpus()
    hash_path = WORK / "targets.txt"
    hash_path.write_text(
        "\n".join(case["md5"] for case in cases) + "\n", encoding="utf-8"
    )
    by_md5 = {case["md5"]: case["password"] for case in cases}
    print(f"目标 {len(cases)} 条；词表 {WORDLIST}")

    # 组合词表：英文基础词 + 中文种子词（验证"原生词表是否该包含种子词表"）
    zh_path = REPO_ROOT / "data" / "wordlists" / "zh-base.txt"
    combined = WORK / "combined-words.txt"
    words = [line.strip() for line in WORDLIST.read_text(encoding="utf-8").splitlines() if line.strip()]
    if zh_path.is_file():
        words += [line.strip() for line in zh_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    combined.write_text("\n".join(dict.fromkeys(words)) + "\n", encoding="utf-8")
    print(f"组合词表 {combined}（{len(dict.fromkeys(words))} 条）")

    rule_sets = [
        ("best66", ["best66.rule"]),
        ("best66+leetspeak", ["best66.rule", "leetspeak.rule"]),
        ("best66+d3ad0ne", ["best66.rule", "d3ad0ne.rule"]),
        ("best66+d3ad0ne+leetspeak", ["best66.rule", "d3ad0ne.rule", "leetspeak.rule"]),
        ("best66+dive", ["best66.rule", "dive.rule"]),
        ("best66+d3ad0ne+dive", ["best66.rule", "d3ad0ne.rule", "dive.rule"]),
        ("best66+dive+rockyou30000", ["best66.rule", "dive.rule", "rockyou-30000.rule"]),
        (
            "best66+d3ad0ne+dive+stacking58",
            ["best66.rule", "d3ad0ne.rule", "dive.rule", "stacking58.rule"],
        ),
        (
            "best66+dive+specific+oscommerce",
            ["best66.rule", "dive.rule", "specific.rule", "oscommerce.rule"],
        ),
        ("best66+combinator+stacking58", ["best66.rule", "combinator.rule", "stacking58.rule"]),
    ]

    results: list[tuple[str, int, int]] = []
    total = len(cases)
    for wordlist, tag in ((WORDLIST, "base81"),):
        print(f"\n--- 词表 {tag} ---", flush=True)
        word_count = len([line for line in wordlist.read_text(encoding="utf-8").splitlines() if line.strip()])
        for name, files in rule_sets:
            paths = [RULES_DIR / rule for rule in files]
            if not all(path.is_file() for path in paths):
                print(f"{name:<32} 跳过（规则文件缺失）", flush=True)
                continue
            counts = [
                len([line for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()])
                for path in paths
            ]
            chain = 1
            for count in counts:
                chain *= count
            outfile = WORK / f"{tag}-{name}.txt"
            recovered = run_hashcat(
                ["-a", "0", str(hash_path), str(wordlist)]
                + [arg for path in paths for arg in ("-r", str(path))],
                outfile,
            )
            results.append((f"{tag}/{name}", recovered, word_count * chain))
            found = (
                [p for p in by_md5.values() if p in outfile.read_text(encoding="utf-8", errors="ignore").splitlines()]
                if outfile.is_file() else []
            )
            print(
                f"{name:<32} 命中 {recovered:>3}/{total}  "
                f"规则链 {'×'.join(str(c) for c in counts)} 键空间≈{word_count * chain:>12}  例：{found[:5]}",
                flush=True,
            )

    # 并集对照：把多份规则合并成一个文件（hashcat 的多个 -r 是规则链，不是并集）
    merged = WORK / "merged.rule"
    merged.write_text(
        "\n".join(
            line
            for rule in ("best66.rule", "dive.rule")
            for line in (RULES_DIR / rule).read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip()
        )
        + "\n",
        encoding="utf-8",
    )
    print("\n--- 并集对照（合并成一个规则文件）---", flush=True)
    for label, rules in (("merged(best66+dive)", merged),):
        outfile = WORK / f"{label}.txt"
        recovered = run_hashcat(
            ["-a", "0", str(hash_path), str(WORDLIST), "-r", str(rules)], outfile
        )
        print(f"{label:<32} 命中 {recovered:>3}/{total}", flush=True)

    print("\n=== 词表 × 掩码（-a 6）单独覆盖 ===")
    for masks in (["?d?d?d?d"], ["?d?d"], ["!"], ["?d?d?d?d", "?d?d", "!"]):
        outfile = WORK / f"hybrid-{'_'.join(masks)}.txt".replace("?", "q")
        if len(masks) == 1:
            args = ["-a", "6", str(hash_path), str(WORDLIST), masks[0]]
        else:
            # 多个掩码必须写成 .hcmask 文件（与适配层一致），否则 hashcat 报 Invalid argument
            mask_file = WORK / "masks.hcmask"
            mask_file.write_text("\n".join(masks) + "\n", encoding="utf-8")
            args = ["-a", "6", str(hash_path), str(WORDLIST), str(mask_file)]
        recovered = run_hashcat(args, outfile)
        print(f"混合掩码 {','.join(masks):<24} 命中 {recovered:>3}/100", flush=True)

    print("\n=== 掩码（-a 3）单独覆盖 ===")
    for mask in ("?d?d?d?d", "?l?l?l?l"):
        recovered = run_hashcat(
            ["-a", "3", str(hash_path), mask], WORK / f"mask-{mask.replace('?', 'q')}.txt"
        )
        print(f"掩码 {mask:<12} 命中 {recovered:>3}/100", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

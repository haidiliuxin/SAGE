"""训练 OMEN / Markov 模型（供 S3 的 `markov` 生成器使用）。

产物目录格式（与 vendored pcfg_cracker 的 `omen.input_file_io.load_rules` 对齐）：

    <output>/
      config.txt      [training_settings] encoding=... / ngram=...
      alphabet.txt    每行一个字符
      IP.level        <level>\t<起始前缀>
      EP.level        <level>\t<可结束前缀>
      CP.level        <level>\t<前缀+下一个字符>
      LN.level        每行一个 level（第 i 行对应口令长度 i）
      manifest.json   语料规模、阶数、各文件 sha256

用法：

    .\\.venv\\Scripts\\python.exe scripts\\train_markov.py \\
        --corpus data/wordlists/zh-base.txt F:\\SA\\tools\\hashcat-7.1.2\\example.dict \\
        --output models/markov-demo --order 3

训练完成后脚本会**自动用项目内的 MarkovGenerator 加载并生成候选**做自检。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_LEVEL = 10
MAX_PASSWORD_CHARS = 64


def read_corpus(paths: list[Path]) -> tuple[list[str], dict[str, int]]:
    passwords: list[str] = []
    stats: dict[str, int] = {}
    for path in paths:
        resolved = Path(path).expanduser()
        if not resolved.is_file():
            print(f"！语料不存在，跳过：{resolved}", file=sys.stderr)
            continue
        count = 0
        for line in resolved.read_text(encoding="utf-8", errors="replace").splitlines():
            value = line.strip()
            if not value or len(value) > MAX_PASSWORD_CHARS:
                continue
            passwords.append(value)
            count += 1
        stats[str(resolved)] = count
    return passwords, stats


def level_for(rank: int, total: int) -> int:
    """按概率排名分档（0 最高、MAX_LEVEL 最低）。"""
    if total <= 1:
        return 0
    level = int(rank / total * (MAX_LEVEL + 1))
    return min(MAX_LEVEL, level)


def train(passwords: list[str], order: int) -> dict[str, str]:
    """按 OMEN 语义训练：上下文长度 = order，config.txt 里 ngram = order + 1。"""
    context_len = order
    alphabet = Counter()
    ip = Counter()
    ep = Counter()
    cp: dict[str, Counter[str]] = {}
    lengths = Counter()

    for password in passwords:
        characters = list(password)
        total = len(characters)
        if not total:
            continue
        alphabet.update(characters)
        lengths[total] += 1
        if total <= context_len:
            # 口令短于上下文：整口令既是起始上下文，也是终止上下文
            prefix = "".join(characters)
            ip[prefix] += 1
            ep[prefix] += 1
            continue
        ip["".join(characters[:context_len])] += 1
        for index in range(context_len, total + 1):
            prefix = "".join(characters[index - context_len : index])
            if index == total:
                # 该上下文之后口令结束
                ep[prefix] += 1
            else:
                cp.setdefault(prefix, Counter())[characters[index]] += 1

    files: dict[str, str] = {}

    # alphabet.txt
    files["alphabet.txt"] = "".join(f"{char}\n" for char, _ in alphabet.most_common())

    # config.txt
    files["config.txt"] = (
        "[training_settings]\n"
        f"encoding = utf-8\n"
        f"ngram = {order + 1}\n"
    )

    # IP / EP / CP：按计数降序分档
    ip_lines: list[str] = []
    for rank, (prefix, _) in enumerate(ip.most_common()):
        ip_lines.append(f"{level_for(rank, len(ip))}\t{prefix}")
    files["IP.level"] = "\n".join(ip_lines) + ("\n" if ip_lines else "")

    ep_lines = [
        f"{level_for(rank, len(ep))}\t{prefix}"
        for rank, (prefix, _) in enumerate(ep.most_common())
    ]
    files["EP.level"] = "\n".join(ep_lines) + ("\n" if ep_lines else "")

    cp_lines: list[str] = []
    for prefix, counter in cp.items():
        ranked = counter.most_common()
        for rank, (char, _) in enumerate(ranked):
            cp_lines.append(f"{level_for(rank, len(ranked))}\t{prefix}{char}")
    files["CP.level"] = "\n".join(cp_lines) + ("\n" if cp_lines else "")

    # LN.level：第 i 行对应长度 i 的档位（不存在的长度按最低概率档处理）
    if lengths:
        ranked_lengths = sorted(lengths.items(), key=lambda item: (-item[1], item[0]))
        rank_by_length = {
            length: rank for rank, (length, _) in enumerate(ranked_lengths)
        }
        max_length = max(lengths)
        ln_lines = [
            str(
                level_for(rank_by_length[length], len(ranked_lengths))
                if length in rank_by_length
                else MAX_LEVEL
            )
            for length in range(1, max_length + 1)
        ]
        files["LN.level"] = "\n".join(ln_lines) + "\n"
    else:
        files["LN.level"] = "0\n"
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", nargs="+", required=True, help="语料文件（每行一个口令）")
    parser.add_argument("--output", required=True, help="模型输出目录")
    parser.add_argument("--order", type=int, default=3, help="阶数 2～5（默认 3）")
    parser.add_argument("--verify-count", type=int, default=10, help="自检生成候选数")
    args = parser.parse_args()

    if not 2 <= args.order <= 5:
        print("阶数必须在 2～5 之间", file=sys.stderr)
        return 2

    passwords, stats = read_corpus([Path(item) for item in args.corpus])
    if not passwords:
        print("语料为空，未生成模型", file=sys.stderr)
        return 1
    # 去重后训练（重复口令会放大权重）
    unique = list(dict.fromkeys(passwords))
    files = train(unique, args.order)

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest_files: dict[str, dict[str, object]] = {}
    for name, content in files.items():
        target = output / name
        # 固定 LF 换行，保证 manifest 里的 sha256 与实际字节一致。
        target.write_text(content, encoding="utf-8", newline="\n")
        manifest_files[name] = {
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "lines": content.count("\n"),
            "bytes": len(content.encode("utf-8")),
        }
    manifest = {
        "model_type": "omen-markov",
        "order": args.order,
        "max_level": MAX_LEVEL,
        "corpus": stats,
        "corpus_lines": len(passwords),
        "corpus_unique": len(unique),
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "files": manifest_files,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"模型已写入 {output}")
    print(
        "语料：{0} 条（去重 {1}）；阶数 {2}；IP {3} / EP {4} / CP {5} / LN {6} 行".format(
            len(passwords),
            len(unique),
            args.order,
            manifest_files["IP.level"]["lines"],
            manifest_files["EP.level"]["lines"],
            manifest_files["CP.level"]["lines"],
            manifest_files["LN.level"]["lines"],
        )
    )

    # ---------------- 自检：用项目内生成器加载并生成候选 ----------------
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from sage_pass.generators.markov import MarkovGenerator  # noqa: PLC0415
    from sage_pass.generators.base import GeneratorPrepareRequest  # noqa: PLC0415
    from sage_pass.enums import StrategyId  # noqa: PLC0415

    generator = MarkovGenerator(str(output), default_order=args.order)
    state = generator.prepare(
        GeneratorPrepareRequest(
            strategy_id=StrategyId.S3,
            parameters={"min_level": 0, "max_level": args.order},
        )
    )
    produced: list[str] = []
    while len(produced) < args.verify_count and not generator.exhausted(state):
        batch = generator.next_batch(state, args.verify_count - len(produced))
        produced.extend(batch.candidates)
    print(f"自检生成 {len(produced)} 条候选：{produced[:args.verify_count]}")
    if not produced:
        print("自检失败：模型未生成任何候选", file=sys.stderr)
        return 1
    info = getattr(generator, "model_info", None) or getattr(
        generator, "_model_info", None
    )
    print(f"模型信息：{info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

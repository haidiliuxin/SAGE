"""校验模型目录（OMEN/Markov 与 PCFG）是否可用。

用法：

    .\\.venv\\Scripts\\python.exe scripts\\check_models.py models\\markov-demo
    .\\.venv\\Scripts\\python.exe scripts\\check_models.py models\\markov-demo --generate 5

检查项：
1. 必需文件齐全（OMEN：config.txt / alphabet.txt / IP.level / EP.level / CP.level / LN.level）；
2. manifest.json 存在且各文件 sha256 与内容一致；
3. 用项目内生成器实际加载并生成候选（可选 --generate）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OMEN_FILES = ("config.txt", "alphabet.txt", "IP.level", "EP.level", "CP.level", "LN.level")


def check_files(directory: Path) -> list[str]:
    problems: list[str] = []
    for name in OMEN_FILES:
        target = directory / name
        if not target.is_file():
            problems.append(f"缺少文件：{name}")
        elif target.stat().st_size == 0:
            problems.append(f"文件为空：{name}")
    return problems


def check_manifest(directory: Path) -> list[str]:
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return ["缺少 manifest.json（无法校验完整性；可重新运行训练脚本生成）"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"manifest.json 解析失败：{exc}"]
    problems: list[str] = []
    for name, info in (manifest.get("files") or {}).items():
        target = directory / name
        if not target.is_file():
            problems.append(f"manifest 列出的文件缺失：{name}")
            continue
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != info.get("sha256"):
            problems.append(f"sha256 不匹配：{name}")
    return problems


def try_generate(directory: Path, count: int) -> list[str]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from sage_pass.enums import StrategyId  # noqa: PLC0415
    from sage_pass.generators.base import GeneratorPrepareRequest  # noqa: PLC0415
    from sage_pass.generators.markov import MarkovGenerator  # noqa: PLC0415

    generator = MarkovGenerator(str(directory))
    order = int(generator._model_info["order"])  # noqa: SLF001
    state = generator.prepare(
        GeneratorPrepareRequest(
            strategy_id=StrategyId.S3,
            parameters={"min_level": 0, "max_level": order},
        )
    )
    produced: list[str] = []
    while len(produced) < count and not generator.exhausted(state):
        batch = generator.next_batch(state, count - len(produced))
        produced.extend(batch.candidates)
    return produced


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", help="模型目录")
    parser.add_argument("--generate", type=int, default=0, help="加载并生成 N 条候选")
    args = parser.parse_args()

    directory = Path(args.model).expanduser()
    if not directory.is_dir():
        print(f"目录不存在：{directory}", file=sys.stderr)
        return 2

    problems = check_files(directory) + check_manifest(directory)
    if problems:
        for item in problems:
            print(f"[失败] {item}")
        return 1
    print("[OK] 文件齐全且与 manifest 校验一致")

    if args.generate:
        try:
            produced = try_generate(directory, args.generate)
        except Exception as exc:  # noqa: BLE001 - 校验脚本需要展示任何加载错误
            print(f"[失败] 加载/生成失败：{type(exc).__name__}: {exc}")
            return 1
        print(f"[OK] 生成 {len(produced)} 条候选：{produced[: args.generate]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

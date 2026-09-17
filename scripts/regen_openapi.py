"""重新生成或校验 docs/openapi.json 与运行时 app.openapi() 的一致性。

用法（在仓库根目录执行）::

    python scripts/regen_openapi.py          # 校验，不一致时以非零退出码结束
    python scripts/regen_openapi.py --write  # 以运行时契约重写 docs/openapi.json

之所以提供这个脚本，是因为契约漂移（例如新增 Task 字段却没同步 openapi.json）
在评审中反复出现；机器可读契约必须由应用本身生成。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPO_ROOT / "docs" / "openapi.json"


def _load_app():
    src = REPO_ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    os.environ.setdefault("SAGE_DATABASE_URL", "sqlite:///:memory:")
    from sage_pass.main import app  # noqa: PLC0415 - 延迟导入以便先设置 sys.path

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="用运行时契约覆盖 docs/openapi.json（默认只校验）",
    )
    args = parser.parse_args(argv)

    app = _load_app()
    runtime = app.openapi()
    serialized = json.dumps(runtime, indent=2, ensure_ascii=False) + "\n"

    if args.write:
        OPENAPI_PATH.write_text(serialized, encoding="utf-8", newline="\n")
        print(f"openapi.json 已更新：{OPENAPI_PATH}")
        return 0

    if not OPENAPI_PATH.exists():
        print(f"缺少 {OPENAPI_PATH}，请运行 python scripts/regen_openapi.py --write")
        return 1

    current = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    if current == runtime:
        print("openapi sync: True")
        return 0

    print("openapi sync: False")
    print("docs/openapi.json 与运行时契约不一致，请运行 --write 重新生成。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

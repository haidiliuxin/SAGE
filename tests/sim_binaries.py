"""生成可控的 hashcat / zip2john 仿真脚本，供第二周真实执行测试使用。

这些脚本通过环境变量控制行为（是否恢复、是否放慢、输出 $zip2$ 还是
$pkzip2$），从而在不安装真实工具的前提下验证适配器的启动、停止、超时与
结果解析逻辑。仿真脚本只模拟 CLI 契约，不代表“本机已安装真实工具”。
"""

from __future__ import annotations

from pathlib import Path

FAKE_HASHCAT = r'''# -*- coding: utf-8 -*-
import json
import os
import sys
import time


def arg_value(flag):
    try:
        index = sys.argv.index(flag)
    except ValueError:
        return None
    try:
        return sys.argv[index + 1]
    except IndexError:
        return None


outfile = arg_value("--outfile")
slow = float(os.environ.get("FAKE_HASHCAT_SLOW", "0"))
no_recover = os.environ.get("FAKE_HASHCAT_NO_RECOVER", "") == "1"
hex_plain = os.environ.get("FAKE_HASHCAT_HEX_PLAIN", "") == "1"

wordlist = sys.argv[-1]
target_path = sys.argv[-2]

with open(wordlist, encoding="utf-8") as fh:
    candidates = [line.rstrip("\r\n") for line in fh]
with open(target_path, encoding="utf-8") as fh:
    targets = [line.rstrip("\r\n") for line in fh if line.strip()]

total = len(candidates)
if slow:
    time.sleep(slow)

print(json.dumps({"progress": [total, 0], "percent": 100}))
sys.stdout.flush()

if not no_recover and outfile and candidates and targets:
    plain = candidates[0]
    if hex_plain:
        plain = "$HEX[" + plain.encode("utf-8").hex().upper() + "]"
    with open(outfile, "w", encoding="utf-8") as fh:
        fh.write(targets[0] + "\t" + plain + "\n")
sys.exit(0)
'''

FAKE_ZIP2JOHN = r'''# -*- coding: utf-8 -*-
import os
import sys
import time

kind = os.environ.get("FAKE_ZIP2JOHN_KIND", "winzip")
slow = float(os.environ.get("FAKE_ZIP2JOHN_SLOW", "0"))
archive = sys.argv[-1] if sys.argv else ""

if slow:
    time.sleep(slow)

if kind == "winzip":
    sample = (
        "$zip2$*0*3*0*b4f8f2f2f2*00000000*1*0*0*0*0*0*0*0*0*"
        "*0*0*0*$/zip2$"
    )
    print(f"{archive}:{sample}")
    sys.exit(0)
if kind == "pkzip":
    print(f"{archive}:$pkzip2$1*2*3*4*5*6*7*8*9*10*11*12")
    sys.exit(1)
if kind == "garbage":
    sys.stdout.write("Could not find a hash for this file?\n")
    sys.stderr.write("zip2john exit 1: nothing usable\n")
    sys.exit(1)
sys.exit(1)
'''


def write_sim_scripts(directory: Path) -> tuple[Path, Path]:
    """写入 hashcat 与 zip2john 仿真脚本，返回 (hashcat_path, zip2john_path)。"""
    directory.mkdir(parents=True, exist_ok=True)
    hashcat_script = directory / "fake_hashcat.py"
    zip_script = directory / "fake_zip2john.py"
    hashcat_script.write_text(FAKE_HASHCAT, encoding="utf-8")
    zip_script.write_text(FAKE_ZIP2JOHN, encoding="utf-8")
    return hashcat_script, zip_script

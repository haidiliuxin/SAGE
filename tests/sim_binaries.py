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

log_path = os.environ.get("FAKE_HASHCAT_LOG", "")
if log_path:
    with open(log_path, "a", encoding="utf-8") as lf:
        lf.write(json.dumps({
            "argv": sys.argv[1:],
            "hash_type": arg_value("--hash-type"),
            "attack_mode": arg_value("--attack-mode"),
            "targets": targets,
            "candidate_count": total,
        }, ensure_ascii=False) + "\n")

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
    # 传统 PKZIP 单文件压缩：结构取自真实 zip2john 输出（数据段为占位十六进制）
    sample = (
        "$pkzip2$1*1*2*0*4bb2*51d9*9f1629fc*0*42*8*4bb2*9f16*5402*"
        + "072c1709d649b2fb93c3cafadd2f59eab322b73bb48cd6840a7ec2d6e0d6bfb1"
        + "*$/pkzip2$"
    )
    print(f"{archive}:{sample}")
    sys.exit(0)
if kind == "pkzip_stored":
    sample = (
        "$pkzip2$1*1*2*0*1d1*1c5*eda7a8de*0*28*0*1d1*eda7*5096*"
        + "1dea673da43d9fc7e2be1a1f4f664269fceb6cb88723a97408ae1fe07f774d31"
        + "*$/pkzip2$"
    )
    print(f"{archive}:{sample}")
    sys.exit(0)
if kind == "pkzip_multi":
    sample = (
        "$pkzip2$3*1*1*0*8*24*a425*8827*"
        + "d1730095cd829e245df04ebba6c52c0573d49d3bbeab6cb385b7fa8a28dcccd3098bfdd7*"
        + "1*0*8*24*2a74*882a*"
        + "51281ac874a60baedc375ca645888d29780e20d4076edd1e7154a99bde982152a736311f*"
        + "*$/pkzip2$"
    )
    print(f"{archive}:{sample}")
    sys.exit(0)
if kind == "pkzip_checksum":
    sample = (
        "$pkzip2$8*1*1*0*8*24*a425*8827*"
        + "3bd479d541019c2f32395046b8fbca7e*"
        + "1*0*8*24*2a74*882a*"
        + "537af57c30fd9fd4b3eefa9ce55b6bff*"
        + "*$/pkzip2$"
    )
    print(f"{archive}:{sample}")
    sys.exit(0)
if kind == "garbage":
    sys.stdout.write("Could not find a hash for this file?\n")
    sys.stderr.write("zip2john exit 1: nothing usable\n")
    sys.exit(1)
sys.exit(1)
'''


FAKE_JOHN_EXTRACTOR = r'''# -*- coding: utf-8 -*-
"""仿真 pdf2john / office2john：按 FAKE_JOHN_KIND 输出样例 Hash 行。"""

import os
import sys
import time

kind = os.environ.get("FAKE_JOHN_KIND", "pdf2")
slow = float(os.environ.get("FAKE_JOHN_SLOW", "0"))
target = sys.argv[-1] if sys.argv else ""

if slow:
    time.sleep(slow)

SAMPLES = {
    "pdf1": "$pdf$1*2*40*-1*1*16*abcdef0123456789*32*0011223344556677",
    "pdf2": "$pdf$2*3*128*-1028*1*16*abcdef0123456789*32*0011223344556677*32*8899aabbccddeeff",
    "pdf3": "$pdf$3*3*128*1*16*abcdef0123456789*32*0011223344556677",
    "pdf4": "$pdf$4*4*128*-1028*1*16*abcdef0123456789*32*0011223344556677*32*8899aabbccddeeff",
    "office2007": "$office$*2007*20*128*16*abcdef0123456789*0011223344556677*8899aabbccddeeff",
    "office2010": "$office$*2010*100000*128*16*abcdef0123456789*0011223344556677*8899aabbccddeeff",
    "office2013": "$office$*2013*100000*256*16*abcdef0123456789*0011223344556677*8899aabbccddeeff",
    "oldoffice1": "$oldoffice$1*abcdef0123456789*0011223344556677*8899aabbccddeeff",
    "empty": "",
}

sample = SAMPLES.get(kind, "")
if sample:
    print(f"{target}:{sample}")
    sys.exit(0)
sys.stderr.write("no hash found (file may be unencrypted)\n")
sys.exit(1)
'''


def write_john_extractor_scripts(directory: Path) -> tuple[Path, Path]:
    """写入 pdf2john / office2john 仿真脚本，返回 (pdf_path, office_path)。"""
    directory.mkdir(parents=True, exist_ok=True)
    pdf_script = directory / "fake_pdf2john.py"
    office_script = directory / "fake_office2john.py"
    pdf_script.write_text(FAKE_JOHN_EXTRACTOR, encoding="utf-8")
    office_script.write_text(FAKE_JOHN_EXTRACTOR, encoding="utf-8")
    return pdf_script, office_script


def write_sim_scripts(directory: Path) -> tuple[Path, Path]:
    """写入 hashcat 与 zip2john 仿真脚本，返回 (hashcat_path, zip2john_path)。"""
    directory.mkdir(parents=True, exist_ok=True)
    hashcat_script = directory / "fake_hashcat.py"
    zip_script = directory / "fake_zip2john.py"
    hashcat_script.write_text(FAKE_HASHCAT, encoding="utf-8")
    zip_script.write_text(FAKE_ZIP2JOHN, encoding="utf-8")
    return hashcat_script, zip_script

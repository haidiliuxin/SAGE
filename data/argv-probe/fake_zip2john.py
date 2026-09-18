# -*- coding: utf-8 -*-
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

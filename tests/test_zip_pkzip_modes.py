"""传统 PKZIP（ZipCrypto，`$pkzip2$`）的 hashcat 模式判定。

期望值来自 hashcat 自身：样例哈希取自 `hashcat --example-hashes -m <mode>`，
兼容性结论（17225 通吃单/多文件与压缩/未压缩，仅校验和变体需要 17230）由
在 hashcat 7.1.2 上逐模式实测得到。
"""

from __future__ import annotations

import pytest

from sage_pass.zip_adapter import (
    PKZIP_CHECKSUM_ONLY_MODE,
    PKZIP_MIXED_MODE,
    PKZIP_SINGLE_COMPRESSED_MODE,
    PKZIP_SINGLE_UNCOMPRESSED_MODE,
    _resolve_pkzip2,
    pkzip2_hashcat_mode,
)

DATA = "072c1709d649b2fb93c3cafadd2f59eab322b73bb48cd6840a7ec2d6e0d6bfb1"

# hashcat --example-hashes: 17200 / 17210 的头部结构（数据段压缩为等长占位）
SINGLE_COMPRESSED = f"$pkzip2$1*1*2*0*e3*1c5*eda7a8de*0*28*8*e3*eda7*5096*{DATA}*$/pkzip2$"
SINGLE_UNCOMPRESSED = f"$pkzip2$1*1*2*0*1d1*1c5*eda7a8de*0*28*0*1d1*eda7*5096*{DATA}*$/pkzip2$"
# 17220：三个文件全部 deflate
MULTI_COMPRESSED = (
    "$pkzip2$3*1*1*0*8*24*a425*8827*"
    "d1730095cd829e245df04ebba6c52c0573d49d3bbeab6cb385b7fa8a28dcccd3098bfdd7*"
    "1*0*8*24*2a74*882a*"
    "51281ac874a60baedc375ca645888d29780e20d4076edd1e7154a99bde982152a736311f*"
    "*$/pkzip2$"
)
# 17225：首文件 stored、次文件 deflate（混合）
MULTI_MIXED = (
    "$pkzip2$3*1*1*0*0*24*3e2c*3ef8*"
    "0619e9d17ff3f994065b99b1fa8aef41c056edf9fa4540919c109742dcb32f797fc90ce0*"
    "1*0*8*24*431a*3f26*"
    "18e2461c0dbad89bd9cc763067a020c89b5e16195b1ac5fa7fb13bd246d000b6833a2988*"
    "*$/pkzip2$"
)
# 17230：每个文件的数据段只有 16 字节校验和
MULTI_CHECKSUM_ONLY = (
    "$pkzip2$8*1*1*0*8*24*a425*8827*3bd479d541019c2f32395046b8fbca7e*"
    "1*0*8*24*2a74*882a*537af57c30fd9fd4b3eefa9ce55b6bff*"
    "*$/pkzip2$"
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (SINGLE_COMPRESSED, PKZIP_SINGLE_COMPRESSED_MODE),
        (SINGLE_UNCOMPRESSED, PKZIP_SINGLE_UNCOMPRESSED_MODE),
        (MULTI_COMPRESSED, PKZIP_MIXED_MODE),
        (MULTI_MIXED, PKZIP_MIXED_MODE),
        (MULTI_CHECKSUM_ONLY, PKZIP_CHECKSUM_ONLY_MODE),
    ],
)
def test_pkzip2_mode_matches_hashcat_modes(value, expected):
    assert pkzip2_hashcat_mode(value) == expected


def test_pkzip2_unknown_compression_method_falls_back_to_compressed():
    # 压缩方式 12（bzip2）不在支持范围：交给 17200，让 hashcat 明确报错而不是静默跳过。
    value = SINGLE_COMPRESSED.replace("*0*28*8*", "*0*28*12*")
    assert pkzip2_hashcat_mode(value) == PKZIP_SINGLE_COMPRESSED_MODE


def test_pkzip2_garbage_falls_back_to_mixed_mode():
    assert pkzip2_hashcat_mode("$pkzip2$not-a-hash*$/pkzip2$") == PKZIP_MIXED_MODE


def test_pkzip2_mixed_archive_keeps_one_mode_and_reports_the_rest():
    kept, mode, warnings = _resolve_pkzip2(
        (SINGLE_COMPRESSED, SINGLE_UNCOMPRESSED, MULTI_CHECKSUM_ONLY)
    )
    # hashcat 一次运行只接受一个模式：保留多数派，并说明被排除的目标数。
    assert mode in {PKZIP_SINGLE_COMPRESSED_MODE, PKZIP_SINGLE_UNCOMPRESSED_MODE}
    assert len(kept) == 1
    assert warnings and "未纳入" in warnings[0]


def test_pkzip2_homogeneous_archive_needs_no_warning():
    second = SINGLE_COMPRESSED.replace(DATA, DATA[:-8] + "deadbeef")
    kept, mode, warnings = _resolve_pkzip2((SINGLE_COMPRESSED, second))
    assert mode == PKZIP_SINGLE_COMPRESSED_MODE
    assert kept == (SINGLE_COMPRESSED, second)
    assert warnings == ()

#!/usr/bin/env python3
"""
一次性脚本：下载 python.org embeddable Python ZIP，解出 OpenSSL DLL 到 vendor/。

为什么不用 conda-forge / Anaconda 自带的 DLL？
    Windows 上的 OpenSSL 编译时若启用了 -DOPENSSL_USE_APPLINK，libcrypto
    会要求加载它的主程序提供 OPENSSL_Applink 符号；Python 的 python.exe 本身
    提供，所以 `python main.py` 没事；但 PyInstaller 的 bootloader 不提供，
    invest_tool.exe 启动就崩 "OPENSSL_Uplink: no OPENSSL_Applink"。

    实测：Anaconda Inc 和 conda-forge 编译的 OpenSSL 都启用了 USE_APPLINK；
    只有 python.org 官方 Python 自带的 OpenSSL 没启用，跟 PyInstaller 兼容。

为什么不让你装 python.org Python？
    完全不必要——只需要 2 个 DLL 文件。本脚本下载官方 embeddable ZIP
    （单文件、自包含、不安装），解出 DLL 后直接删 ZIP。整个过程：
        - 不动 venv
        - 不动 conda 环境
        - 不在系统装任何东西
        - 项目目录里只多 vendor/openssl/ 两个 dll 文件（约 5MB）

只需运行一次：
    venv/Scripts/python.exe vendor_openssl.py

之后常规打包不变（python release.py / python build.py）。

升级 OpenSSL 时：
    重跑本脚本（会自动下载最新补丁版的 Python embeddable）。
"""
import io
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

# 让中文 + Unicode 符号在 Windows GBK 终端不炸
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

ROOT = Path(__file__).parent.resolve()
VENDOR_DIR = ROOT / 'vendor' / 'openssl'

# 用 Python 3.12.8（与 venv 大版本一致即可，DLL 在大版本内通用）
# 完整 ZIP 列表见：https://www.python.org/downloads/release/python-3128/
PY_VERSION = '3.12.8'
EMBED_ZIP_URL = f'https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip'

# 必须**配套**替换这 4 个文件，光换 .dll 不行：Anaconda 的 _ssl.pyd 跟
# python.org 的 libcrypto 符号布局不一致，会导致 "DLL load failed" 闪退。
# 必须用同一份 build 的 _ssl/_hashlib + libcrypto/libssl，ABI 才匹配。
#
# 注意：保留 python.org 的原始命名（libcrypto-3.dll，不带 -x64 后缀）。
# python.org 的 _ssl.pyd 编译时硬编码依赖 libcrypto-3.dll，不能重命名。
EMBED_FILES = ['_ssl.pyd', '_hashlib.pyd', 'libcrypto-3.dll', 'libssl-3.dll']


# ---- 简易日志 ----

class _C:
    G='\033[92m'; Y='\033[93m'; R='\033[91m'; B='\033[94m'; DIM='\033[2m'; BOLD='\033[1m'; END='\033[0m'

def step(m): print(f"\n{_C.BOLD}{_C.B}▶ {m}{_C.END}")
def ok(m):   print(f"  {_C.G}✓ {m}{_C.END}")
def warn(m): print(f"  {_C.Y}⚠ {m}{_C.END}")
def err(m):  print(f"  {_C.R}✗ {m}{_C.END}")
def dim(m):  print(f"  {_C.DIM}{m}{_C.END}")


def download_with_progress(url):
    """下载 URL 内容到内存，返回 bytes；带简单进度。"""
    dim(f"$ GET {url}")
    req = urllib.request.Request(url, headers={'User-Agent': 'invest-tool-vendor/1.0'})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get('Content-Length', 0))
        chunk_size = 64 * 1024
        buf = bytearray()
        downloaded = 0
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            buf.extend(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / total * 100
                print(f"\r    {downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB  ({pct:.0f}%)",
                      end='', flush=True)
        print()  # 换行
        return bytes(buf)


def main():
    print(f"{_C.BOLD}=== 下载 python.org embeddable Python {PY_VERSION} → 提取 OpenSSL DLL ==={_C.END}")

    step("下载 embeddable ZIP（约 10 MB）")
    try:
        zip_bytes = download_with_progress(EMBED_ZIP_URL)
    except Exception as e:
        err(f"下载失败：{e}")
        err("如果网络有问题，可以手动下载：")
        err(f"    {EMBED_ZIP_URL}")
        err("把 ZIP 放在项目根目录改名为 python_embed.zip，然后重新运行本脚本")
        # 回退：检查本地 python_embed.zip
        local_zip = ROOT / 'python_embed.zip'
        if local_zip.exists():
            warn(f"使用本地 {local_zip.name}")
            zip_bytes = local_zip.read_bytes()
        else:
            sys.exit(1)
    ok(f"下载完成（{len(zip_bytes) / 1024 / 1024:.1f} MB）")

    step("提取 4 个文件（_ssl.pyd / _hashlib.pyd / libcrypto / libssl）")
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)

    extracted = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        for src_name in EMBED_FILES:
            for n in names:
                if os.path.basename(n).lower() == src_name.lower():
                    data = zf.read(n)
                    out_path = VENDOR_DIR / src_name
                    out_path.write_bytes(data)
                    size_kb = len(data) / 1024
                    ok(f"{n}  →  vendor/openssl/{src_name}  ({size_kb:.0f} KB)")
                    extracted.append(src_name)
                    break

    missing = [v for v in EMBED_FILES if v not in extracted]
    if missing:
        err(f"缺失文件：{missing}")
        err("ZIP 内容（前 30 项）：")
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for n in zf.namelist()[:30]:
                dim(f"  {n}")
        sys.exit(1)

    # 写一个版本信息文件
    info_path = VENDOR_DIR / 'README.txt'
    info_path.write_text(
        f"本目录的 DLL 来自 python.org 官方 Python {PY_VERSION} embeddable ZIP，\n"
        f"由 vendor_openssl.py 自动下载并提取。\n\n"
        f"=== 来源 URL ===\n{EMBED_ZIP_URL}\n\n"
        f"=== 用途 ===\n"
        f"PyInstaller 打包后 build.py 会用这两个 DLL 覆盖 dist 里 Anaconda 编译的版本，\n"
        f"解决 OPENSSL_Uplink: no OPENSSL_Applink 崩溃问题。\n"
        f"python.org 官方 build 的 OpenSSL 编译时未启用 -DOPENSSL_USE_APPLINK，\n"
        f"跟 PyInstaller 完全兼容。\n\n"
        f"=== 重新拉取 ===\n"
        f"venv/Scripts/python.exe vendor_openssl.py\n",
        encoding='utf-8'
    )
    ok("README.txt（含版本信息）")

    print(f"\n{_C.BOLD}{_C.G}=== vendor 完成 ==={_C.END}")
    print(f"  下次打包时 build.py 会自动用 vendor/openssl/ 里的 DLL。")
    print(f"  现在跑一次：{_C.BOLD}venv\\Scripts\\python.exe build.py --skip-front{_C.END}")


if __name__ == '__main__':
    main()

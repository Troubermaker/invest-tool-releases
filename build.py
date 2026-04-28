#!/usr/bin/env python3
"""
invest_tool 一键打包脚本。

用法（在项目根目录执行）:
  python build.py                 发布版打包（Cython 保护 + 无 console）
  python build.py --dev           调试版打包（保留 console 窗口看 Python 报错）
  python build.py --skip-front    跳过前端构建（前端无改动时省 30 秒）
  python build.py --skip-cython   跳过 Cython 编译（开发自测用，不推荐发布）
  python build.py --clean         仅清理 build/ dist/ frontend/dist/，不打包
  python build.py --run           打完包立即启动一次 exe
  python build.py -h              查看帮助

流程:
  1. 校验环境（Node/npm、PyInstaller、Cython、spec 文件）
  2. 清理旧产物（build/ + dist/）
  3. 构建前端（npm run build → frontend/dist/）
  4. Cython 编译关键模块为 .pyd（保护 SECRET / Cookies）
     → license_service / _auth 源码改名 .py.bak（PyInstaller 只看到 .pyd）
  5. PyInstaller 打包（invest_tool.spec）
  6. 恢复源码（.py.bak → .py，无论上一步成功失败都执行）
  7. 输出结果汇总（exe 路径 + 目录大小）

产物:
  dist/invest_tool/                整个目录可直接拷走分发
  dist/invest_tool/invest_tool.exe 双击启动
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Windows 控制台默认 GBK 编码，打印中文 + ▶✓ 等 Unicode 字符会炸
# 把标准流强行切换成 UTF-8（Python 3.7+ 支持）
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

ROOT = Path(__file__).parent.resolve()
FRONTEND_DIR = ROOT / 'frontend'
FRONTEND_DIST = FRONTEND_DIR / 'dist'
BUILD_DIR = ROOT / 'build'
DIST_DIR = ROOT / 'dist'
SPEC_FILE = ROOT / 'invest_tool.spec'
SETUP_CYTHON = ROOT / 'setup_cython.py'
EXE_PATH = DIST_DIR / 'invest_tool' / 'invest_tool.exe'

# 与 setup_cython.py 中的 PROTECTED_MODULES 保持一致
CYTHON_MODULES = [
    'services/license_service.py',
    'api_endpoints/_auth.py',
]

# 由 main() 在解析 CLI 后赋值；check_prereq() 用它判断是否需要 Cython
_SKIP_CYTHON = False


# ---- 简易日志（Win10+ 自动支持 ANSI 彩色）----
class _C:
    R = '\033[91m'; G = '\033[92m'; Y = '\033[93m'; B = '\033[94m'
    DIM = '\033[2m'; BOLD = '\033[1m'; END = '\033[0m'


def _enable_ansi():
    """在 Windows cmd 上启用 ANSI 转义序列。"""
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass


def step(msg): print(f"\n{_C.BOLD}{_C.B}▶ {msg}{_C.END}")
def ok(msg):   print(f"  {_C.G}✓ {msg}{_C.END}")
def warn(msg): print(f"  {_C.Y}⚠ {msg}{_C.END}")
def err(msg):  print(f"  {_C.R}✗ {msg}{_C.END}")
def dim(msg):  print(f"  {_C.DIM}{msg}{_C.END}")


def run(cmd, *, cwd=None, env=None, shell=False):
    """串流子进程输出，返回退出码；出错时打印命令方便复现。"""
    dim(f"$ {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    t0 = time.time()
    try:
        rc = subprocess.run(cmd, cwd=cwd, env=env, shell=shell).returncode
    except FileNotFoundError as e:
        err(f"命令未找到：{e}")
        return -1
    dt = time.time() - t0
    dim(f"  → 退出码 {rc}，耗时 {dt:.1f}s")
    return rc


# ---- 各阶段 ----

def check_prereq():
    step("环境检查")
    passed = True

    # Node / npm（Windows 上 npm 是 .cmd，需要 shell=True）
    try:
        r = subprocess.run(['npm', '--version'], capture_output=True, text=True, shell=True)
        ok(f"npm {r.stdout.strip() or '(未知版本)'}")
    except Exception as e:
        err(f"未检测到 npm（{e}）。请先装 Node.js")
        passed = False

    # PyInstaller
    try:
        import PyInstaller
        ok(f"PyInstaller {PyInstaller.__version__}")
    except ImportError:
        err("未安装 PyInstaller。运行：pip install pyinstaller")
        passed = False

    # Cython（除非 --skip-cython，否则必需）
    if not _SKIP_CYTHON:
        try:
            import Cython
            ok(f"Cython {Cython.__version__}")
        except ImportError:
            err("未安装 Cython。运行：pip install cython")
            passed = False

    # spec
    if SPEC_FILE.exists():
        ok(f"spec：{SPEC_FILE.name}")
    else:
        err(f"未找到 {SPEC_FILE.name}")
        passed = False

    if not _SKIP_CYTHON and not SETUP_CYTHON.exists():
        err(f"未找到 {SETUP_CYTHON.name}")
        passed = False

    return passed


def clean_artifacts(include_frontend_dist=False):
    step("清理旧产物")
    targets = [BUILD_DIR, DIST_DIR]
    if include_frontend_dist:
        targets.append(FRONTEND_DIST)
    removed = []
    for p in targets:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(p.relative_to(ROOT).as_posix())
    if removed:
        for r in removed:
            ok(f"删除 {r}/")
    else:
        dim("（无需清理）")


def build_frontend():
    step("构建前端 (Vue → frontend/dist/)")
    if not (FRONTEND_DIR / 'node_modules').exists():
        warn("缺少 frontend/node_modules/，自动执行 npm install")
        if run(['npm', 'install'], cwd=FRONTEND_DIR, shell=True) != 0:
            err("npm install 失败")
            return False
    if run(['npm', 'run', 'build'], cwd=FRONTEND_DIR, shell=True) != 0:
        err("前端构建失败")
        return False
    if not (FRONTEND_DIST / 'index.html').exists():
        err("frontend/dist/index.html 未生成，构建异常")
        return False
    ok("前端产物就绪")
    return True


def compile_cython():
    """
    用 setup_cython.py 把 PROTECTED_MODULES 编译为 .pyd 放到模块原目录下。
    成功返回 True；失败返回 False。
    """
    step("Cython 编译关键模块（保护 SECRET / Cookies）")
    rc = run(
        [sys.executable, str(SETUP_CYTHON), 'build_ext', '--inplace'],
        cwd=ROOT,
    )
    if rc != 0:
        err("Cython 编译失败")
        return False
    # 校验产物
    for py_path in CYTHON_MODULES:
        pyd_dir = ROOT / Path(py_path).parent
        stem = Path(py_path).stem
        # .pyd 名带 ABI tag，如 license_service.cp312-win_amd64.pyd
        pyd_files = list(pyd_dir.glob(f"{stem}.*.pyd"))
        if not pyd_files:
            err(f"未生成 {py_path[:-3]}.*.pyd")
            return False
        ok(f"{py_path} → {pyd_files[0].name}")
    return True


def hide_source_for_pyinstaller():
    """
    把 PROTECTED_MODULES 的 .py 改名 .py.bak，让 PyInstaller 只能看到 .pyd。
    返回已重命名的 (原路径) 列表，用于后续 restore。
    """
    step("隐藏源码（.py → .py.bak）")
    backup_paths = []
    for py_path in CYTHON_MODULES:
        full = ROOT / py_path
        if not full.exists():
            warn(f"{py_path} 不存在（可能上次未恢复），跳过")
            continue
        bak = Path(str(full) + '.bak')
        # 防御：如果 .bak 已存在（上次异常残留），先删掉
        if bak.exists():
            bak.unlink()
        full.rename(bak)
        backup_paths.append(full)
        ok(f"隐藏 {py_path}")
    return backup_paths


def restore_sources(backup_paths):
    """把 .py.bak 恢复为 .py。无论 PyInstaller 成功失败都要调用。"""
    if not backup_paths:
        return
    step("恢复源码（.py.bak → .py）")
    for original in backup_paths:
        bak = Path(str(original) + '.bak')
        if not bak.exists():
            warn(f"{original.relative_to(ROOT)}.bak 不存在，无法恢复")
            continue
        if original.exists():
            # 极端情况：.py 又出现了（不应该），先删掉
            original.unlink()
        bak.rename(original)
        ok(f"恢复 {original.relative_to(ROOT).as_posix()}")


def cleanup_cython_artifacts():
    """
    可选清理：把 .pyd / .lib / .exp 中间产物删掉，保持源码树干净。
    .pyd 在打包结束后已经被 PyInstaller 复制进 dist/，源码目录里的可以删。

    注意：若有进程正在加载 .pyd（如杀软扫描中 / IDE 在跑），删除会失败。
    无关紧要，下次 build 会被覆盖。
    """
    removed = 0
    skipped = 0
    for py_path in CYTHON_MODULES:
        pyd_dir = ROOT / Path(py_path).parent
        stem = Path(py_path).stem
        for pat in (f"{stem}.*.pyd", f"{stem}.*.exp", f"{stem}.*.lib"):
            for f in pyd_dir.glob(pat):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    skipped += 1
    if removed:
        dim(f"  清理了 {removed} 个 Cython 中间产物")
    if skipped:
        warn(f"{skipped} 个 .pyd / .lib 被占用，跳过删除（不影响打包）")


def build_exe(dev_mode):
    label = '调试版 console=True' if dev_mode else '发布版 console=False'
    step(f"PyInstaller 打包 — {label}")
    env = os.environ.copy()
    env['INVEST_BUILD_DEV'] = '1' if dev_mode else '0'
    t0 = time.time()
    rc = subprocess.run(
        [sys.executable, '-m', 'PyInstaller', str(SPEC_FILE), '--noconfirm', '--clean'],
        cwd=ROOT, env=env,
    ).returncode
    dt = time.time() - t0
    if rc != 0:
        err(f"打包失败（{dt:.1f}s，退出码 {rc}）")
        return False
    ok(f"打包完成，耗时 {dt:.1f}s")
    return True


VENDOR_OPENSSL = ROOT / 'vendor' / 'openssl'  # 由 vendor_openssl.py 填充

# 必须配套替换的 4 个文件（与 vendor_openssl.py 的 EMBED_FILES 一致）。
# 4 个文件来自同一份 python.org Python build，ABI 互相匹配。
# 单独换 .dll 不行——会触发 "DLL load failed while importing _ssl"。
# 注意：DLL 名是 libcrypto-3.dll（python.org 命名），不能重命名为带 -x64 的，
# 否则 _ssl.pyd 找不到它的依赖。
OPENSSL_FILES = ['_ssl.pyd', '_hashlib.pyd', 'libcrypto-3.dll', 'libssl-3.dll']

# Anaconda PyInstaller 自动 bundle 进来的同名/旧名文件，替换后该清理掉
ANACONDA_LEFTOVERS = ['libcrypto-3-x64.dll', 'libssl-3-x64.dll']


def fix_openssl_dll_location():
    """
    替换 dist 里 PyInstaller 自动收集的 Anaconda OpenSSL 套件，改用
    vendor/openssl/ 里 python.org 官方 build 的版本。

    为什么必须替换全套（4 个文件，不能光换 .dll）：
        Anaconda 编 libcrypto 时启用了 -DOPENSSL_USE_APPLINK，要求加载它
        的主程序提供 OPENSSL_Applink 符号。Anaconda python.exe 提供了，
        但 PyInstaller bootloader 没提供 → 启动崩 OPENSSL_Uplink。

        python.org 的 libcrypto 没启 USE_APPLINK，与 PyInstaller 兼容。
        但 _ssl.pyd 必须跟 libcrypto 来自同一份 build，ABI 才匹配；
        混用 Anaconda _ssl.pyd + python.org libcrypto 会 "DLL load failed"。

    libcrypto / libssl 同时复制到 exe 同级目录，因为 Windows DLL 搜索里
    "exe 所在目录" 优先级高于 _internal/，挡掉 PATH 上的脏 DLL。
    """
    step("替换 OpenSSL 套件（_ssl/_hashlib/libcrypto/libssl → python.org 版）")
    bundle = DIST_DIR / 'invest_tool'
    internal = bundle / '_internal'
    if not internal.exists():
        warn("dist/invest_tool/_internal/ 不存在，跳过")
        return

    missing_vendored = [f for f in OPENSSL_FILES if not (VENDOR_OPENSSL / f).exists()]
    if missing_vendored:
        err(f"vendor/openssl/ 缺少文件：{missing_vendored}")
        err("请先运行：venv\\Scripts\\python.exe vendor_openssl.py")
        err("（这会从 python.org 下载 embeddable Python 提取 4 个文件，只需一次）")
        sys.exit(1)

    # 1) 用 python.org 版覆盖 / 新增到 dist
    for name in OPENSSL_FILES:
        src = VENDOR_OPENSSL / name
        shutil.copy2(src, internal / name)
        # libcrypto / libssl 还要放到 exe 同级，避免 PATH 上的脏 DLL 抢加载
        if name.endswith('.dll'):
            shutil.copy2(src, bundle / name)
            ok(f"{name}（_internal/ + exe 同级，python.org 版）")
        else:
            ok(f"{name}（_internal/，python.org 版）")

    # 2) 清掉 PyInstaller bundle 进来的 Anaconda 同名旧版 DLL
    #    （文件名 libcrypto-3-x64.dll，跟 python.org 的 libcrypto-3.dll 是不同文件）
    cleaned = 0
    for name in ANACONDA_LEFTOVERS:
        for path in [internal / name, bundle / name]:
            if path.exists():
                try:
                    path.unlink()
                    cleaned += 1
                except OSError:
                    pass
    if cleaned:
        dim(f"  清理 {cleaned} 个 Anaconda 残留 DLL（{', '.join(ANACONDA_LEFTOVERS)}）")


def summarize():
    step("打包结果")
    if not EXE_PATH.exists():
        err(f"未找到 {EXE_PATH}")
        return False
    # 统计目录大小
    total = 0
    for root, _, files in os.walk(DIST_DIR / 'invest_tool'):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    ok(f"exe:  {EXE_PATH}")
    ok(f"体积: {total / (1024 * 1024):.1f} MB（整个 dist/invest_tool/ 目录）")
    dim("整个目录可直接拷给另一台电脑双击运行")
    return True


def launch_exe():
    if not EXE_PATH.exists():
        err("exe 不存在，无法启动")
        return
    step("启动 exe 预览")
    dim(f"$ {EXE_PATH}")
    # Windows 上用 os.startfile 触发，不阻塞脚本
    if os.name == 'nt':
        os.startfile(str(EXE_PATH))
    else:
        subprocess.Popen([str(EXE_PATH)])
    ok("已启动（脚本不等待窗口关闭）")


# ---- 入口 ----

def main():
    parser = argparse.ArgumentParser(
        description='invest_tool 一键打包',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例:\n  python build.py --dev --run\n  python build.py --skip-front',
    )
    parser.add_argument('--dev', action='store_true', help='保留 console 窗口调试')
    parser.add_argument('--skip-front', action='store_true', help='跳过前端构建')
    parser.add_argument('--skip-cython', action='store_true',
                        help='跳过 Cython 编译（开发自测用，发布请勿使用）')
    parser.add_argument('--clean', action='store_true', help='只清理，不打包')
    parser.add_argument('--run', action='store_true', help='打包成功后立即启动 exe')
    args = parser.parse_args()

    # check_prereq() 需要知道是否要校验 Cython
    global _SKIP_CYTHON
    _SKIP_CYTHON = args.skip_cython

    _enable_ansi()
    print(f"{_C.BOLD}=== invest_tool 构建脚本 ==={_C.END}")
    t_start = time.time()

    if args.clean:
        clean_artifacts(include_frontend_dist=True)
        step(f"✓ 清理完成（耗时 {time.time() - t_start:.1f}s）")
        return

    if not check_prereq():
        err("环境检查未通过，终止")
        sys.exit(1)

    clean_artifacts(include_frontend_dist=False)

    if args.skip_front:
        if not (FRONTEND_DIST / 'index.html').exists():
            err("--skip-front 要求 frontend/dist/index.html 已存在")
            sys.exit(1)
        dim("跳过前端构建（复用 frontend/dist/）")
    else:
        if not build_frontend():
            sys.exit(1)

    # —— Cython 保护流程：编译 → 隐藏源码 → PyInstaller → 无论成败恢复源码 —— #
    backup_paths = []
    pyinstaller_ok = False
    try:
        if not args.skip_cython:
            if not compile_cython():
                sys.exit(1)
            backup_paths = hide_source_for_pyinstaller()
        else:
            warn("已跳过 Cython 编译（SECRET / Cookies 将明文打包，不要发布！）")

        pyinstaller_ok = build_exe(dev_mode=args.dev)
    finally:
        # 关键：无论 PyInstaller 成功还是失败，源码必须恢复回来
        restore_sources(backup_paths)
        if not args.skip_cython:
            cleanup_cython_artifacts()

    if not pyinstaller_ok:
        sys.exit(1)

    fix_openssl_dll_location()

    if not summarize():
        sys.exit(1)

    if args.run:
        launch_exe()

    step(f"✓ 全部完成（总耗时 {time.time() - t_start:.1f}s）")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
invest_tool 一键打包脚本。

用法（在项目根目录执行）:
  python build.py                 发布版打包（无 console 窗口、体积略小）
  python build.py --dev           调试版打包（保留 console 窗口看 Python 报错）
  python build.py --skip-front    跳过前端构建（前端无改动时省 30 秒）
  python build.py --clean         仅清理 build/ dist/ frontend/dist/，不打包
  python build.py --run           打完包立即启动一次 exe
  python build.py -h              查看帮助

流程:
  1. 校验环境（Node/npm、PyInstaller、spec 文件）
  2. 清理旧产物（build/ + dist/）
  3. 构建前端（npm run build → frontend/dist/）
  4. PyInstaller 打包（invest_tool.spec）
  5. 输出结果汇总（exe 路径 + 目录大小）

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
EXE_PATH = DIST_DIR / 'invest_tool' / 'invest_tool.exe'


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

    # spec
    if SPEC_FILE.exists():
        ok(f"spec：{SPEC_FILE.name}")
    else:
        err(f"未找到 {SPEC_FILE.name}")
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
    parser.add_argument('--clean', action='store_true', help='只清理，不打包')
    parser.add_argument('--run', action='store_true', help='打包成功后立即启动 exe')
    args = parser.parse_args()

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

    if not build_exe(dev_mode=args.dev):
        sys.exit(1)

    if not summarize():
        sys.exit(1)

    if args.run:
        launch_exe()

    step(f"✓ 全部完成（总耗时 {time.time() - t_start:.1f}s）")


if __name__ == '__main__':
    main()

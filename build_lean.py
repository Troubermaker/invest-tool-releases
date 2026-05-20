#!/usr/bin/env python3
"""
精简打包脚本 —— 把 ML 库临时卸载，让 PyInstaller 打不出来，结束后自动装回。

用法（venv 激活后）：
    python build_lean.py                # 等价于 release.py，但 ML 库不进包
    python build_lean.py --bump patch   # 升小版本
    python build_lean.py --publish --git-tag   # 完整发布

设计：
  1. 记录当前 venv 装的 ML 库版本（防止重装时版本漂移）
  2. pip uninstall lightgbm shap pandas sklearn matplotlib scipy ...
  3. 调 release.py（透传所有参数）
  4. **无论成功失败**都把 ML 库装回去（finally 兜底）

开发期 ML 用法不变：
  - 训练：python -m services.ml_signal_filter
  - 健康检查/IC 评估：从 UI 触发
  - 都正常工作，因为打包后这台机器上 ML 库还是装着的
"""
import argparse
import subprocess
import sys
import os
import json
from pathlib import Path

# Windows 默认 GBK 控制台无法打印 ▶ ✓ 等 Unicode 字符，强制 stdout/stderr 为 UTF-8
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 需要临时卸载的 ML 相关包（要跟 invest_tool.spec 的 excludes 保持一致）
# ⚠ 不要加 pandas —— pytdx.hq 顶层 import pandas，卸了之后管理员 TDX K 线会崩。
ML_PACKAGES = [
    'lightgbm',
    'shap',
    'scikit-learn',
    'matplotlib',
    'scipy',
    'numba',       # shap 依赖
    'llvmlite',    # numba 依赖
    'joblib',      # sklearn 依赖
    'threadpoolctl',
]

ROOT = Path(__file__).parent.resolve()
PY = sys.executable
PIP = [PY, '-m', 'pip']
SNAPSHOT_FILE = ROOT / '.ml_pkg_snapshot.json'


def _C():
    class _:
        R='\033[91m'; G='\033[92m'; Y='\033[93m'; B='\033[94m'; END='\033[0m'; BOLD='\033[1m'
    return _

C = _C()


def step(msg): print(f"\n{C.BOLD}{C.B}▶ {msg}{C.END}")
def ok(msg):   print(f"{C.G}  ✓ {msg}{C.END}")
def warn(msg): print(f"{C.Y}  ⚠ {msg}{C.END}")
def err(msg):  print(f"{C.R}  ✗ {msg}{C.END}")


def snapshot_installed():
    """记录当前装的 ML 包 + 版本，方便结束后精确装回。"""
    installed = {}
    out = subprocess.run(PIP + ['list', '--format=json'], capture_output=True, text=True)
    if out.returncode != 0:
        warn('pip list 失败，跳过版本快照')
        return installed
    try:
        items = json.loads(out.stdout)
        # name 大小写归一化
        idx = {x['name'].lower(): x['version'] for x in items}
        for pkg in ML_PACKAGES:
            ver = idx.get(pkg.lower())
            if ver:
                installed[pkg] = ver
    except Exception as e:
        warn(f'快照解析失败: {e}')
    SNAPSHOT_FILE.write_text(json.dumps(installed, indent=2), encoding='utf-8')
    return installed


def uninstall_ml(installed):
    """临时卸 ML 包。"""
    if not installed:
        ok('venv 里没装 ML 包，无需卸载')
        return
    print(f"  即将卸载: {', '.join(installed.keys())}")
    res = subprocess.run(PIP + ['uninstall', '-y'] + list(installed.keys()))
    if res.returncode != 0:
        warn('部分包卸载失败，继续打包流程（可能仍会被打入）')


def restore_ml(installed):
    """根据快照精确装回。"""
    if not installed:
        ok('无 ML 包需要恢复')
        return
    pkgs = [f'{name}=={ver}' for name, ver in installed.items()]
    print(f"  装回: {', '.join(pkgs)}")
    res = subprocess.run(PIP + ['install'] + pkgs)
    if res.returncode != 0:
        err('装回部分失败！下次开发前手动 pip install -r requirements-ml.txt')
    else:
        ok('ML 包已恢复，可继续开发')


def main():
    # 透传所有参数给 release.py，不解析
    extra_args = sys.argv[1:]

    print(f"{C.BOLD}=== invest_tool 精简打包（ML 临时卸载）==={C.END}")

    # 1. 快照
    step("快照 venv 中的 ML 包版本")
    installed = snapshot_installed()
    if installed:
        for n, v in installed.items():
            print(f"    · {n}=={v}")
    else:
        warn('未检测到 ML 包，直接跑 release.py')

    # 2. 卸 + 打包 + 装回（finally 兜底）
    try:
        if installed:
            step("临时卸载 ML 包")
            uninstall_ml(installed)
            ok('卸载完成，开始打包')

        step("调用 release.py")
        release_py = ROOT / 'release.py'
        cmd = [PY, str(release_py)] + extra_args
        print(f"  $ {' '.join(cmd)}")
        rc = subprocess.run(cmd).returncode

        if rc == 0:
            ok('release.py 成功')
        else:
            err(f'release.py 退出码 {rc}')
    finally:
        if installed:
            step("恢复 ML 包（无论上一步成败都执行）")
            restore_ml(installed)
        if SNAPSHOT_FILE.exists():
            try: SNAPSHOT_FILE.unlink()
            except Exception: pass

    print(f"\n{C.BOLD}=== 完成 ==={C.END}")
    print(f"{C.DIM if hasattr(C, 'DIM') else ''}发布产物在 release/ 目录{C.END}")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        err('用户中断 —— ML 包可能未恢复，运行 `pip install -r requirements-ml.txt` 手动恢复')
        sys.exit(1)

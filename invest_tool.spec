# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置。推荐走 build.py 一键脚本，别直接调 pyinstaller。

直调用法：
    pyinstaller invest_tool.spec --noconfirm --clean
通过环境变量切换模式：
    INVEST_BUILD_DEV=1      保留 console 窗口（方便看 Python 报错）
    INVEST_BUILD_DEV=0/未设 发布版，无 console

输出在 dist/invest_tool/ 目录下，双击 invest_tool.exe 即可运行。

关键点：
- onedir 模式（不是 onefile）：启动快，pywebview + WebView2 兼容性更好
- 把 frontend/dist 原样打进去，main.py 会 detect 到并加载
- 关键模块由 build.py 调用 setup_cython.py 编译为 .pyd，PyInstaller 阶段
  对应 .py 已被改名 .py.bak，所以本 spec 看到的是机器码扩展模块，不会
  把 SECRET / Cookies 以源码形式打入分发包：
    services/license_service.py    → license_service.cp312-win_amd64.pyd
    api_endpoints/_auth.py         → _auth.cp312-win_amd64.pyd
"""
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all

DEV_MODE = os.environ.get('INVEST_BUILD_DEV') == '1'

# ---- 打包入口 ----
block_cipher = None

# ---- 对 SSL/HTTPS 相关的复杂库做"整包收集" ----
# cryptography 自带 OpenSSL DLL，PyInstaller 静态分析会漏掉一些运行时需要的二进制，
# 导致第一次 HTTPS 请求触发 "OPENSSL_Uplink: no OPENSSL_Applink" 进程崩溃。
# collect_all 把 datas / binaries / hiddenimports 全拿到，确保不缺件。
# 显式列出 Cython 编译过的扩展模块。.py 在打包阶段会被改名 .py.bak，
# PyInstaller 静态分析可能扫不到，加进来保险。
hiddenimports = [
    'schedule',
    'services.license_service',   # Cython 保护：HMAC SECRET
    'api_endpoints._auth',        # Cython 保护：所有 Cookie / Token
]
datas = [
    ('frontend/dist', 'frontend/dist'),       # 前端打包产物
    ('assets/icon.ico', 'assets'),            # 应用图标（pywebview 窗口运行时也用）
    ('assets/icon.png', 'assets'),
]
binaries = []

for pkg in ('webview', 'cryptography', 'certifi', 'charset_normalizer', 'urllib3'):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 开发期测试文件，不要进打包
        'scratch', 'tests',
        # ============ ML 库不打包 ============
        # 这些是开发期训练/分析工具（services/ml_signal_filter.py 用），
        # 装进 venv 是为了开发期跑 `python -m services.ml_signal_filter`，
        # 但分发给最终用户没必要 —— 体积能从 ~700MB 降到 ~180MB
        # 后端的 ml_predict_service / ml_health_service 已经 catch ImportError 优雅降级，
        # 用户在 exe 里看到的是 "mlScore=null" / "未安装 ML 支持库" 提示
        #
        # ⚠ pandas 不能 exclude —— pytdx.hq 顶层 import pandas，
        #   一旦 exclude，admin 模式调 TDX 看 K 线 / 分时图会直接 ModuleNotFoundError。
        #   （ml_predict_service / ml_health_service 都是函数内 import pandas，会受影响但优雅降级。
        #    pytdx 是顶层 import，没法降级。）
        'lightgbm', 'shap', 'sklearn', 'scipy', 'matplotlib',
        'numpy.distutils',           # numpy 内部测试工具
        'IPython', 'jupyter', 'notebook', 'jedi',   # 调试/REPL 工具
        'PIL.ImageQt', 'PIL.ImageTk',# pillow 的 Qt/Tk 集成，pywebview 不用
        # 'numpy' 不能 exclude —— pywebview / 部分日志格式化用到
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='invest_tool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # UPX 压缩有时会被杀软误报，关闭
    console=DEV_MODE,       # DEV_MODE=True 时保留命令行窗口看 Python 日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='invest_tool',
)

import os
import sys

# ========= 修复 OPENSSL_Uplink 崩溃（必须最早执行，先于任何 import）=========
# PyInstaller 把 _internal/libcrypto-3-x64.dll 放在子目录里，Windows 的 DLL
# 搜索顺序里 PATH 优先级高于子目录，所以如果用户 PATH 里有 miniconda /
# Git Bash 自带的 libcrypto-3-x64.dll，cryptography 加载到的会是错的那一份，
# 触发 "OPENSSL_Uplink: no OPENSSL_Applink" 弹窗崩溃。
# 解决：把 _internal/ 强制加到 DLL 搜索路径最前面。
if getattr(sys, 'frozen', False):
    _bundle_dir = os.path.dirname(sys.executable)
    _internal_dir = os.path.join(_bundle_dir, '_internal')
    if os.path.isdir(_internal_dir):
        # AddDllDirectory（Win8+）—— 比改 PATH 更可靠，不污染子进程环境
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(_internal_dir)
            except (OSError, ValueError):
                pass
        # 双保险：PATH 也前置一下（兼容老的 LoadLibrary 调用）
        os.environ['PATH'] = _internal_dir + os.pathsep + os.environ.get('PATH', '')

# ========= PyInstaller windowed 模式 stdout 兜底 =========
# console=False 打包后 sys.stdout / sys.stderr 为 None，任何 print()、pywebview 的
# debug 日志、scheduler 后台线程的 print 都会"写 None 报错 → 进程崩溃"。
# 在最早期就把两者重定向到 exe 旁边的 log 文件，从此所有输出安全落盘。
if getattr(sys, 'frozen', False) and (sys.stdout is None or sys.stderr is None):
    _log_path = os.path.join(os.path.dirname(sys.executable), 'invest_tool.log')
    try:
        _f = open(_log_path, 'a', encoding='utf-8', buffering=1)
        sys.stdout = _f
        sys.stderr = _f
    except Exception:
        # 实在打不开就用黑洞 null 设备，总之不能让 print 崩
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = sys.stdout

# ========= 加载 .env（必须在 ai_service / baidu_ocr_service 等读 os.getenv 之前）=========
# 极简实现：纯 stdlib 解析，避免引入 python-dotenv 依赖
# - 文件位置：项目根目录或 exe 旁边的 .env
# - 优先级：真实 OS 环境变量 > .env（已设的不被覆盖，便于临时调试）
# - 语法：每行 KEY=VALUE，# 开头是注释，支持 'value' / "value" 引号包裹
def _load_dotenv():
    # PyInstaller 打包后用 sys.executable 旁边的 .env；开发态用工作目录
    if getattr(sys, 'frozen', False):
        env_path = os.path.join(os.path.dirname(sys.executable), '.env')
    else:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, 'r', encoding='utf-8-sig') as f:   # utf-8-sig 自动剥 BOM
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    continue
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.strip()
                # 剥引号
                if (len(val) >= 2 and
                    ((val[0] == '"' and val[-1] == '"') or
                     (val[0] == "'" and val[-1] == "'"))):
                    val = val[1:-1]
                # 已被 OS 环境变量设过的不覆盖（让真实环境变量优先级更高）
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as e:
        print(f'[WARN] .env 加载失败：{e}')

_load_dotenv()

import webview
import socket
from api import Api
import scheduler

def is_port_open(host, port):
    """检测指定端口是否开放"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def get_screen_size():
    """获取主显示器分辨率（Windows 下用 user32，其它平台用兜底）。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # 让进程感知 DPI 缩放，否则 GetSystemMetrics 在高分屏上会返回"逻辑像素"的小值
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080  # 兜底


DEFAULT_BOSS_KEY = 'ctrl+b'  # 2 键组合，默认值


def setup_boss_key(window):
    """
    从 user_preferences 读取 boss_key 配置，注册全局 hotkey。
    若用户没配过用 DEFAULT_BOSS_KEY；若用户配的 hotkey 注册失败（冲突），降级到默认。
    """
    from services import watchlist_service, boss_key_service
    saved = watchlist_service.get_preference('boss_key', DEFAULT_BOSS_KEY)
    try:
        boss_key_service.register(window, saved)
    except Exception as e:
        print(f"[WARN] 用户配置的老板键 '{saved}' 注册失败（{e}），降级到 {DEFAULT_BOSS_KEY}")
        try:
            boss_key_service.register(window, DEFAULT_BOSS_KEY)
        except Exception as e2:
            print(f"[WARN] 默认老板键也注册失败: {e2}")

def main():
    # Start the background data fetcher scheduling daemon 
    scheduler.start_background_daemon()
    
    api = Api()
    
    # 获取 frontend/dist/index.html 的绝对路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    dist_path = os.path.join(current_dir, "frontend", "dist", "index.html")
    
    # 如果 frontend/dist 存在，则加载打包后的页面；否则加载本地 vite 开发服务器
    if os.path.exists(dist_path):
        url = f"file:///{dist_path.replace(os.sep, '/')}"
    else:
        url = "http://localhost:5173"
        # 监测前端环境是否启动
        if not is_port_open("127.0.0.1", 5173):
            print("\n" + "!" * 60)
            print("【启动监测】未检测到前端开发服务器！")
            print("-" * 60)
            print("当前处于 [开发模式] (未找到 frontend/dist)")
            print("请确保您已在另一个终端运行了：")
            print("  cd frontend")
            print("  npm run dev")
            print("!" * 60 + "\n")
            # 也可以选择此时 sys.exit(1) 或者继续显示 pywebview 的错误页面

    # 计算窗口居中位置（pywebview 默认不一定居中，手动算更可靠）
    win_w, win_h = 1440, 900
    screen_w, screen_h = get_screen_size()
    x = max(0, (screen_w - win_w) // 2)
    y = max(0, (screen_h - win_h) // 2)

    # 应用图标：开发态从源码 assets，打包后从 _internal/assets（spec 里 datas 已带）
    # 注意：icon 是给 webview.start() 用的（不是 create_window），下方 webview.start 处再传
    if getattr(sys, 'frozen', False):
        icon_path = os.path.join(os.path.dirname(sys.executable), '_internal', 'assets', 'icon.ico')
        if not os.path.exists(icon_path):
            icon_path = os.path.join(os.path.dirname(sys.executable), 'assets', 'icon.ico')
    else:
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'icon.ico')

    window = webview.create_window(
        title="量化复盘与盯盘终端",
        url=url,
        js_api=api,
        width=win_w,
        height=win_h,
        x=x,
        y=y,
        min_size=(800, 700),    # 「摸鱼模式」最小尺寸 —— 用户能拖到桌面边缘隐藏
        background_color='#fcfcfc',  # 纯净白灰浅色背景
    )
    
    api.set_window(window)
    # 注册老板键（全局快捷键，从 user_preferences 读配置；可在设置页修改）
    setup_boss_key(window)
    # 启动时按需预热缓存（盘后 / 非交易日打开 app 时，先后台抓最新交易日数据）
    scheduler.warm_cache_on_startup_async()

    # 系统托盘：关闭按钮 = 隐藏到托盘，真退出走托盘菜单
    from services import tray_service
    if os.path.exists(icon_path):
        tray_service.init(window, icon_path)
        # 拦截关闭事件（pywebview 6.x：events.closing 返 False 取消关闭）
        try:
            window.events.closing += tray_service.on_window_closing
        except Exception as e:
            print(f'[WARN] 注册 closing 事件失败：{e}（关闭按钮将直接退出）')
    # debug 模式自动策略：
    #   python main.py                → debug=True （开发）
    #   打包后 invest_tool.exe         → debug=False（商用发布默认关闭 DevTools）
    #   打包后 invest_tool.exe --debug → debug=True （维护人员排查问题时显式开启）
    is_packaged = getattr(sys, 'frozen', False)
    debug_mode = (not is_packaged) or ('--debug' in sys.argv)
    # icon 参数只在某些 pywebview 版本支持，try/except 兜底避免 TypeError
    try:
        webview.start(debug=debug_mode, icon=icon_path if os.path.exists(icon_path) else None)
    except TypeError:
        # 老版本 pywebview 不认 icon kwarg；图标依赖 PyInstaller spec 里的 exe icon
        webview.start(debug=debug_mode)

if __name__ == '__main__':
    main()

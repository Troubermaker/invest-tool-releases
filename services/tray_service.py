"""
系统托盘服务（Windows / macOS / Linux 通用，基于 pystray）。

功能：
  - 应用启动时在系统托盘显示图标
  - 点击关闭按钮 → 隐藏窗口（不退出，仍在托盘）
  - 左键点击托盘图标 → 显示窗口
  - 右键托盘 → 菜单：[显示主窗口] / [退出]

用法：
    from services import tray_service
    tray_service.init(window, icon_path)   # 在 webview.start 之前调用
    # 注册窗口 closing 事件
    window.events.closing += tray_service.on_window_closing
"""
import logging
import threading

logger = logging.getLogger(__name__)

_tray_icon = None       # pystray.Icon 实例
_window = None          # pywebview window 实例
_real_quit_requested = False    # True 时 closing 事件不再拦截，正常退出


def _load_icon_image(icon_path):
    """读 ICO/PNG 转成 PIL Image（pystray 需要）。失败返默认色块图。"""
    from PIL import Image
    try:
        return Image.open(icon_path)
    except Exception as e:
        logger.warning(f'托盘图标加载失败 {icon_path}：{e}，使用占位')
        # 返回一个 64×64 红色色块作为兜底
        return Image.new('RGBA', (64, 64), (220, 38, 38, 255))


def _show_window():
    """显示并激活窗口。"""
    if _window is None:
        return
    try:
        _window.show()
        _window.restore()       # 如果之前最小化了
    except Exception as e:
        logger.warning(f'显示窗口失败：{e}')


def _quit_app(icon=None, item=None):
    """真正退出应用 — 不再拦截 closing。"""
    global _real_quit_requested
    _real_quit_requested = True
    try:
        if _tray_icon is not None:
            _tray_icon.stop()
    except Exception:
        pass
    try:
        if _window is not None:
            _window.destroy()
    except Exception:
        pass


PREF_MIN_TO_TRAY = 'app.minimize_to_tray_on_close'  # bool，默认 True


def on_window_closing():
    """
    pywebview window.events.closing 回调。
    根据用户偏好 `app.minimize_to_tray_on_close` 决定行为：
      - True（默认）：隐藏到托盘（返 False 取消关闭）
      - False：直接退出（返 None / True 放行）

    托盘菜单"退出"会先设 _real_quit_requested = True，本回调放行，进程退出。
    """
    if _real_quit_requested:
        return None     # 真退出，放行

    # 读偏好（懒加载 watchlist_service 避免循环导入）
    try:
        from services import watchlist_service
        minimize = watchlist_service.get_preference(PREF_MIN_TO_TRAY, True)
    except Exception:
        minimize = True   # 默认行为：隐藏到托盘

    if not minimize:
        # 用户选择了"X 直接退出"
        _quit_app()
        return None

    # 隐藏到托盘
    try:
        _window.hide()
    except Exception as e:
        logger.warning(f'隐藏到托盘失败：{e}')
        return None
    return False


def init(window, icon_path):
    """
    初始化托盘。在 webview.start() 之前调用。
    Args:
        window: pywebview window 实例
        icon_path: ICO 或 PNG 文件路径
    """
    global _tray_icon, _window
    _window = window

    try:
        import pystray
    except ImportError:
        logger.warning('pystray 未安装，托盘功能不可用（关闭按钮直接退出）')
        return None

    image = _load_icon_image(icon_path)

    menu = pystray.Menu(
        pystray.MenuItem('显示主窗口', lambda i, it: _show_window(), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('退出', _quit_app),
    )

    _tray_icon = pystray.Icon(
        name='invest_tool',
        icon=image,
        title='量化复盘与盯盘终端',     # hover tooltip
        menu=menu,
    )

    # 在独立后台线程跑，不阻塞主线程的 webview.start()
    threading.Thread(target=_tray_icon.run, daemon=True, name='tray-icon').start()
    logger.info('系统托盘已就绪')
    return _tray_icon


def is_real_quit_requested():
    """供外部判断是否处于"真退出"状态（避免重复拦截）。"""
    return _real_quit_requested

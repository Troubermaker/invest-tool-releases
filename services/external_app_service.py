"""
联动外部桌面行情软件（通达信 / 同花顺等）。

工作原理：
  1. 枚举 Windows 顶层窗口，按 title 模糊匹配关键字找到目标软件
  2. 激活窗口（前台 + 取消最小化）
  3. 模拟键盘输入 6 位股票代码 + Enter
  4. 通达信/同花顺收到键入后会自动跳转到该股票

模糊匹配策略：每个目标维护多个关键字，命中任一即可。
方便适配各种券商定制版（如「东方财富通达信」、「华泰证券同花顺」等）。

仅 Windows。Mac/Linux 调用直接报 NOT_SUPPORTED。
"""
import logging
import platform
import time

logger = logging.getLogger(__name__)


# ============ 各软件的关键字（小写匹配，互不影响）============
# 加新版本 / 新券商定制版只要往这里加关键字即可
APP_KEYWORDS = {
    'tdx': [
        '通达信',           # 标准版 / 大部分券商版本
        'tdx',              # 偶有英文版/调试窗
        # 常见券商定制（title 通常含 "通达信" 但保险加几个）
        '金融终端',
    ],
    'ths': [
        '同花顺',           # 标准版
        'hexin',            # 内部代号
        'ifind',            # iFinD 专业版
        'wencai',           # 问财
    ],
}

APP_DISPLAY_NAME = {'tdx': '通达信', 'ths': '同花顺'}


def _is_windows():
    return platform.system() == 'Windows'


def _enum_top_windows():
    """枚举所有可见且有 title 的顶层窗口，返回 [(hwnd, title), ...]。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    results = []

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if title:
            results.append((hwnd, title))
        return True

    user32.EnumWindows(EnumWindowsProc(_callback), 0)
    return results


def _find_window(target):
    """按关键字模糊找窗口。返回 (hwnd, title) 或 (None, None)。"""
    keywords = APP_KEYWORDS.get(target, [])
    if not keywords:
        return None, None
    for hwnd, title in _enum_top_windows():
        title_lower = title.lower()
        for kw in keywords:
            if kw.lower() in title_lower:
                return hwnd, title
    return None, None


def _activate_window(hwnd):
    """带兜底的激活：处理最小化、SetForegroundWindow 失败 等。"""
    import ctypes
    user32 = ctypes.windll.user32
    SW_RESTORE = 9

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    # SetForegroundWindow 在某些 focus 受限场景会失败，
    # 兜底：AttachThreadInput 后再 SetForegroundWindow（专业级方案）
    try:
        # 简单路径：直接尝试
        if user32.SetForegroundWindow(hwnd):
            return True
    except Exception:
        pass

    # 兜底：先 BringWindowToTop 再 SetForegroundWindow
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception as e:
        logger.warning(f'激活窗口失败 hwnd={hwnd}: {e}')
        return False


def app_status():
    """探测两个软件是否运行中，给前端显示状态。"""
    if not _is_windows():
        return {
            'platform_supported': False,
            'tdx': {'running': False, 'title': ''},
            'ths': {'running': False, 'title': ''},
        }
    out = {'platform_supported': True}
    for k in ('tdx', 'ths'):
        hwnd, title = _find_window(k)
        out[k] = {'running': hwnd is not None, 'title': title or ''}
    return out


def jump_to_stock(target, code):
    """
    切到目标软件 + 输入股票代码 + 跳转。
    Args:
        target: 'tdx' / 'ths'
        code: 6 位股票代码
    Returns:
        {'ok': True/False, 'reason': str?, 'title': str?}
    """
    if not _is_windows():
        return {'ok': False, 'reason': 'NOT_WINDOWS', 'msg': '联动功能仅 Windows 可用'}

    code = (str(code) or '').strip()
    if not code or not code.isdigit() or len(code) != 6:
        return {'ok': False, 'reason': 'BAD_CODE', 'msg': f'非法代码 {code!r}'}

    hwnd, title = _find_window(target)
    if not hwnd:
        return {
            'ok': False,
            'reason': 'NOT_RUNNING',
            'msg': f'{APP_DISPLAY_NAME.get(target, target)} 未运行',
        }

    if not _activate_window(hwnd):
        return {'ok': False, 'reason': 'ACTIVATE_FAILED', 'msg': '激活窗口失败'}

    # 等焦点切完（SetForegroundWindow 是异步的，立即敲键盘可能落到旧焦点窗口）
    time.sleep(0.08)

    try:
        import keyboard
        keyboard.write(code, delay=0.005)
        time.sleep(0.03)
        keyboard.send('enter')
    except Exception as e:
        logger.warning(f'模拟键盘失败：{e}')
        return {'ok': False, 'reason': 'KEYBOARD_FAILED', 'msg': str(e)}

    return {'ok': True, 'title': title}

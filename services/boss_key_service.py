"""
老板键（全局 hotkey）注册与切换。

模块级状态持有 pywebview 窗口引用 + 当前 hotkey，支持运行时热切换：
    register(window, hotkey)   首次注册（main.py 启动时）
    update(new_hotkey)         用户改了配置，前端 API 调用这个
    get_current()              读当前 hotkey

快捷键按下时触发隐藏 / 恢复窗口切换。
"""
import logging

logger = logging.getLogger(__name__)

_state = {
    'window': None,
    'current_hotkey': None,
    'hidden': False,
}


def register(window, hotkey):
    """启动时调用。传入 pywebview 窗口引用，并注册初始 hotkey。"""
    _state['window'] = window
    _apply(hotkey)


def update(hotkey):
    """用户改了 hotkey。先去掉旧的，再注册新的。失败会抛出。"""
    if _state['window'] is None:
        raise RuntimeError("boss_key_service 未初始化（register 还没调用）")
    _apply(hotkey)


def get_current():
    return _state.get('current_hotkey')


def _apply(hotkey):
    try:
        import keyboard
    except ImportError:
        logger.warning("keyboard 库不可用，老板键禁用")
        return

    # 先移除已有的 hotkey（如果有）
    old = _state.get('current_hotkey')
    if old:
        try:
            keyboard.remove_hotkey(old)
        except Exception as e:
            logger.warning(f"移除旧 hotkey '{old}' 失败: {e}")

    if not hotkey:
        _state['current_hotkey'] = None
        return

    # 注册新 hotkey
    try:
        keyboard.add_hotkey(hotkey, _toggle)
        _state['current_hotkey'] = hotkey
        logger.info(f"老板键已注册: {hotkey}")
    except Exception as e:
        _state['current_hotkey'] = None
        logger.warning(f"注册 hotkey '{hotkey}' 失败: {e}")
        raise


def _toggle():
    w = _state.get('window')
    if not w:
        return
    try:
        if _state['hidden']:
            w.show()
            _state['hidden'] = False
        else:
            w.hide()
            _state['hidden'] = True
    except Exception as e:
        logger.warning(f"老板键切换失败: {e}")

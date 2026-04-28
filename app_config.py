"""
App 级配置（持久化在 %APPDATA%\\InvestTool\\config.json）。

存什么：
    {
        "data_dir": "D:\\MyData\\InvestTool"  # 用户自定义的数据目录路径；省略则用默认 %APPDATA%\\InvestTool
    }

为什么 config 单独放（不跟 invest_data.db 一起）：
    config 是"数据目录在哪"的元信息——它必须有一个**固定**位置，否则 chicken-and-egg：
    "config 说数据在 X" 但 "config 自己在 X 里" 就成了死循环。
    所以 config 永远在 %APPDATA%\\InvestTool\\config.json，与用户选的数据目录无关。
"""
import json
import os
import sys
from pathlib import Path


def _config_dir() -> Path:
    """config.json 所在目录。"""
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        return Path(base) / 'InvestTool'
    return Path.home() / '.invest_tool'


CONFIG_PATH = _config_dir() / 'config.json'


def _load() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')


def default_data_dir() -> Path:
    """默认数据目录 = %APPDATA%\\InvestTool\\（同 config 在同一文件夹下）。"""
    return _config_dir()


def get_data_dir() -> str:
    """
    返回当前生效的数据目录路径（绝对路径字符串）。
    优先用 config 里的 data_dir；不存在 / 不可访问就回落到默认。
    """
    cfg = _load()
    custom = cfg.get('data_dir')
    if custom:
        try:
            p = Path(custom)
            p.mkdir(parents=True, exist_ok=True)
            # 简单可写性测试
            test = p / '.write_test'
            test.write_text('ok', encoding='utf-8')
            test.unlink()
            return str(p)
        except (OSError, PermissionError):
            pass  # 自定义路径有问题 → 回落默认
    default = default_data_dir()
    default.mkdir(parents=True, exist_ok=True)
    return str(default)


def set_data_dir(new_path: str):
    """改写 data_dir 配置。下次启动 db.py 会读这个新值。"""
    cfg = _load()
    cfg['data_dir'] = str(Path(new_path).resolve())
    _save(cfg)


def reset_data_dir():
    """恢复默认（删除 config 里的 data_dir 字段）。"""
    cfg = _load()
    cfg.pop('data_dir', None)
    _save(cfg)

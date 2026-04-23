"""
用户数据的导入 / 导出。

导出范围：自选（groups + stocks）、持仓（accounts + positions）、用户偏好。
不包含 market_cache —— 那是纯 HTTP 缓存，换机器后自动重新拉取即可。

导入策略：replace —— 清空上述用户表再写入，保留原有自增 id 以确保外键关系可恢复。
"""
import json
import logging
from datetime import datetime

import db

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# 顺序敏感：导出/清空按相反顺序处理外键依赖
# groups → stocks（stocks 依赖 groups），accounts → positions（positions 依赖 accounts）
USER_TABLES = [
    'watchlist_groups',
    'watchlist_stocks',
    'portfolio_accounts',
    'portfolio_positions',
    'user_preferences',
]


def export_user_data():
    """把用户表全部导出为一个 dict（可直接序列化为 JSON）。"""
    conn = db.get_db()
    c = conn.cursor()
    result = {
        'schema_version': SCHEMA_VERSION,
        'exported_at': datetime.now().isoformat(timespec='seconds'),
    }
    for t in USER_TABLES:
        c.execute(f'SELECT * FROM {t}')
        result[t] = [dict(r) for r in c.fetchall()]
    conn.close()
    return result


def import_user_data(data, mode='replace'):
    """
    从 dict 恢复用户数据。
    返回 {table_name: inserted_count} 用于前端展示。

    校验：
    - schema_version 不匹配则直接报错（向前兼容由后续版本处理）
    - mode 目前只支持 'replace'
    """
    if not isinstance(data, dict):
        raise ValueError("导入数据格式错误：期望 JSON 对象")
    v = data.get('schema_version')
    if v != SCHEMA_VERSION:
        raise ValueError(f"数据版本不匹配（当前 {SCHEMA_VERSION}，文件 {v}），拒绝导入")
    if mode != 'replace':
        raise NotImplementedError(f"暂只支持 replace 模式，收到: {mode}")

    conn = db.get_db()
    c = conn.cursor()
    try:
        c.execute('PRAGMA foreign_keys = ON')
        # 清空：逆序（先清依赖方再清被依赖方）
        for t in reversed(USER_TABLES):
            c.execute(f'DELETE FROM {t}')
        # 清空自增计数器，让导入的显式 id 从干净状态开始
        c.execute("DELETE FROM sqlite_sequence WHERE name IN ({})".format(
            ','.join(['?'] * len(USER_TABLES))), USER_TABLES)
        # 插入：正序
        counts = {}
        for t in USER_TABLES:
            rows = data.get(t) or []
            counts[t] = len(rows)
            if not rows:
                continue
            cols = list(rows[0].keys())
            placeholders = ','.join(['?'] * len(cols))
            col_names = ','.join(cols)
            stmt = f'INSERT INTO {t} ({col_names}) VALUES ({placeholders})'
            for row in rows:
                c.execute(stmt, [row.get(k) for k in cols])
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# =========== 文件 I/O 包装（给原生文件对话框用）=========== #

def write_backup_to_file(path):
    """导出并写入指定路径。返回每张表条数。"""
    data = export_user_data()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {t: len(data.get(t) or []) for t in USER_TABLES}


def read_backup_from_file(path):
    """读取 JSON 文件返回 dict。不做导入，只解析。"""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def import_from_file(path, mode='replace'):
    """读 + 导入一体。"""
    return import_user_data(read_backup_from_file(path), mode=mode)

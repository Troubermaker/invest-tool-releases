"""
自选股管理服务。

功能：
- 分组 CRUD + 拖拽重排
- 分组内股票 CRUD + 拖拽重排
- 用户偏好（列顺序等）持久化

数据存储：sqlite 三张表 watchlist_groups / watchlist_stocks / user_preferences
"""
import json
import logging
from datetime import datetime

import db

logger = logging.getLogger(__name__)


# =========== 分组 Groups =========== #

def get_groups():
    """返回所有分组（含股票数 count），按 sort_order 升序。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        SELECT g.id, g.name, g.sort_order, g.created_at,
               COALESCE(COUNT(s.id), 0) AS count
        FROM watchlist_groups g
        LEFT JOIN watchlist_stocks s ON s.group_id = g.id
        GROUP BY g.id, g.name, g.sort_order, g.created_at
        ORDER BY g.sort_order ASC, g.id ASC
    ''')
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_stocks_deduped():
    """'全部自选'虚拟分组：所有分组的股票去重合并（按 code），按最早加入时间排序。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        SELECT s.code,
               MIN(s.name) AS name,
               MIN(s.added_price) AS added_price,
               MIN(s.added_at) AS added_at,
               MIN(s.remark) AS remark,
               MAX(s.alert_above) AS alert_above,
               MIN(s.alert_below) AS alert_below,
               GROUP_CONCAT(g.name, '|') AS group_names
        FROM watchlist_stocks s
        JOIN watchlist_groups g ON g.id = s.group_id
        GROUP BY s.code
        ORDER BY MIN(s.added_at) ASC
    ''')
    rows = c.fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        # group_names 是 '|'-分隔的，便于前端标记股票属于哪些组
        d['groups'] = d.pop('group_names', '').split('|') if d.get('group_names') else []
        results.append(d)
    return results


def get_all_stocks_count():
    """'全部自选'的股票去重数。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('SELECT COUNT(DISTINCT code) AS n FROM watchlist_stocks')
    n = c.fetchone()['n']
    conn.close()
    return n


def create_group(name):
    """创建新分组。sort_order 自动设为当前最大 + 1（排到最后）。返回新 group 的 id。"""
    name = (name or '').strip()
    if not name:
        raise ValueError("分组名称不能为空")
    conn = db.get_db()
    c = conn.cursor()
    c.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM watchlist_groups')
    next_order = c.fetchone()['m'] + 1
    c.execute('INSERT INTO watchlist_groups (name, sort_order) VALUES (?, ?)', (name, next_order))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id


def rename_group(group_id, new_name):
    new_name = (new_name or '').strip()
    if not new_name:
        raise ValueError("分组名称不能为空")
    conn = db.get_db()
    c = conn.cursor()
    c.execute('UPDATE watchlist_groups SET name = ? WHERE id = ?', (new_name, group_id))
    conn.commit()
    conn.close()


def delete_group(group_id):
    """删除分组。由于建表时设了 FOREIGN KEY ... ON DELETE CASCADE，组内股票自动清除。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('PRAGMA foreign_keys = ON')
    c.execute('DELETE FROM watchlist_groups WHERE id = ?', (group_id,))
    conn.commit()
    conn.close()


def reorder_groups(ordered_ids):
    """按传入 id 列表顺序更新 sort_order。"""
    conn = db.get_db()
    c = conn.cursor()
    for i, gid in enumerate(ordered_ids):
        c.execute('UPDATE watchlist_groups SET sort_order = ? WHERE id = ?', (i, gid))
    conn.commit()
    conn.close()


# =========== 股票 Stocks =========== #

def get_group_stocks(group_id):
    """返回某分组下的股票列表（按 sort_order 升序）。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        SELECT id, group_id, code, name, added_price, remark, sort_order, added_at,
               alert_above, alert_below
        FROM watchlist_stocks
        WHERE group_id = ?
        ORDER BY sort_order ASC, id ASC
    ''', (group_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_stock(group_id, code, name='', added_price=None, remark='', added_at=None):
    """向分组添加股票。如果同组已有该代码则静默跳过（基于 UNIQUE 约束）。

    added_at 接受 ISO 字符串（如 '2026-05-19T10:23:00.000Z'）。未传则使用 SQLite
    默认 CURRENT_TIMESTAMP（UTC now）。批量入库时建议从前端统一传一个时刻，
    避免逐行 INSERT 的微秒差异，也避免服务器/客户端时钟漂移。
    """
    code = (code or '').strip()
    if not code:
        raise ValueError("股票代码不能为空")
    conn = db.get_db()
    c = conn.cursor()
    c.execute('SELECT id FROM watchlist_groups WHERE id = ?', (group_id,))
    if not c.fetchone():
        conn.close()
        raise ValueError(f"分组 {group_id} 不存在")
    c.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM watchlist_stocks WHERE group_id = ?', (group_id,))
    next_order = c.fetchone()['m'] + 1
    try:
        if added_at:
            c.execute(
                'INSERT INTO watchlist_stocks (group_id, code, name, added_price, remark, sort_order, added_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (group_id, code, name, added_price, remark or '', next_order, added_at)
            )
        else:
            c.execute(
                'INSERT INTO watchlist_stocks (group_id, code, name, added_price, remark, sort_order) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (group_id, code, name, added_price, remark or '', next_order)
            )
        conn.commit()
    except Exception as e:
        logger.info(f"股票 {code} 已在分组 {group_id}，忽略: {e}")
    finally:
        conn.close()


def update_stock(group_id, code, *, name=None, added_price=None, remark=None, added_at=None):
    """编辑股票：仅更新传入的非 None 字段。added_at 接受 'YYYY-MM-DD' 或 ISO 字符串。"""
    conn = db.get_db()
    c = conn.cursor()
    updates, params = [], []
    if name is not None:
        updates.append('name = ?'); params.append(name)
    if added_price is not None:
        updates.append('added_price = ?'); params.append(added_price)
    if remark is not None:
        updates.append('remark = ?'); params.append(remark)
    if added_at is not None:
        updates.append('added_at = ?'); params.append(added_at)
    if not updates:
        conn.close()
        return
    params.extend([group_id, code])
    c.execute(f'UPDATE watchlist_stocks SET {", ".join(updates)} WHERE group_id = ? AND code = ?', params)
    conn.commit()
    conn.close()


def remove_stock(group_id, code):
    conn = db.get_db()
    c = conn.cursor()
    c.execute('DELETE FROM watchlist_stocks WHERE group_id = ? AND code = ?', (group_id, code))
    conn.commit()
    conn.close()


def reorder_stocks(group_id, ordered_codes):
    """按传入 code 列表顺序更新分组内 sort_order。"""
    conn = db.get_db()
    c = conn.cursor()
    for i, code in enumerate(ordered_codes):
        c.execute(
            'UPDATE watchlist_stocks SET sort_order = ? WHERE group_id = ? AND code = ?',
            (i, group_id, code)
        )
    conn.commit()
    conn.close()


# =========== 用户偏好 Preferences =========== #

def get_preference(key, default=None):
    """读偏好（JSON 反序列化）。不存在返回 default。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('SELECT value FROM user_preferences WHERE key = ?', (key,))
    row = c.fetchone()
    conn.close()
    if not row:
        return default
    try:
        return json.loads(row['value'])
    except Exception:
        return default


def set_preference(key, value):
    """写偏好（自动 JSON 序列化）。"""
    data = json.dumps(value, ensure_ascii=False)
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO user_preferences (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    ''', (key, data, datetime.now().isoformat()))
    conn.commit()
    conn.close()

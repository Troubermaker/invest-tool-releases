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

# 按"业务分区"聚合表：导出/导入以分区为粒度，避免用户理解成散碎的 5 张表
SECTION_TABLES = {
    'watchlist':   ['watchlist_groups', 'watchlist_stocks'],
    'portfolio':   ['portfolio_accounts', 'portfolio_positions'],
    'preferences': ['user_preferences'],
}
ALL_SECTIONS = list(SECTION_TABLES.keys())

# 顺序敏感：导出/清空按相反顺序处理外键依赖
USER_TABLES = [t for section in ALL_SECTIONS for t in SECTION_TABLES[section]]


def export_user_data(sections=None):
    """
    导出指定分区的用户数据。
    sections: 'watchlist' / 'portfolio' / 'preferences' 的子集列表；None = 全部
    未包含的分区其对应的表 key 在结果 dict 中不出现（隐私即结构表达）。
    """
    if sections is None:
        sections = list(ALL_SECTIONS)
    # 校验 + 去重保持顺序
    sections = [s for s in ALL_SECTIONS if s in sections]
    if not sections:
        raise ValueError("至少选择一个分区导出")

    tables_to_include = []
    for s in sections:
        tables_to_include.extend(SECTION_TABLES[s])

    conn = db.get_db()
    c = conn.cursor()
    result = {
        'schema_version': SCHEMA_VERSION,
        'exported_at': datetime.now().isoformat(timespec='seconds'),
        'sections': sections,
    }
    for t in tables_to_include:
        c.execute(f'SELECT * FROM {t}')
        result[t] = [dict(r) for r in c.fetchall()]
    conn.close()
    return result


def import_user_data(data, mode='replace'):
    """
    从 dict 恢复用户数据。
    mode:
      - 'replace'：清空上述用户表再全量写入（最安全用于首次迁移）
      - 'merge'：按"业务键"（分组名 / 账户名 / 分组+code / 账户+code）做 upsert，
                 备份值覆盖本地同键字段；仅本地有的条目保留；偏好跳过
    返回每张表的统计信息（结构随 mode 不同）。
    """
    if not isinstance(data, dict):
        raise ValueError("导入数据格式错误：期望 JSON 对象")
    v = data.get('schema_version')
    if v != SCHEMA_VERSION:
        raise ValueError(f"数据版本不匹配（当前 {SCHEMA_VERSION}，文件 {v}），拒绝导入")
    if mode not in ('replace', 'merge'):
        raise ValueError(f"未知 mode: {mode}（支持 replace / merge）")

    conn = db.get_db()
    c = conn.cursor()
    try:
        c.execute('PRAGMA foreign_keys = ON')
        if mode == 'replace':
            return _do_replace(c, data, conn)
        else:
            return _do_merge(c, data, conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _do_replace(c, data, conn):
    """
    替换模式：**只清空并重写备份里存在的分区**。
    如果备份是"仅自选"的文件，导入后持仓数据完好 —— 避免误杀。
    """
    # 只动备份里实际存在的表
    present = [t for t in USER_TABLES if t in data]
    if not present:
        conn.commit()
        return {}
    # 清空：逆序（先清依赖方再清被依赖方）
    for t in reversed(present):
        c.execute(f'DELETE FROM {t}')
    c.execute("DELETE FROM sqlite_sequence WHERE name IN ({})".format(
        ','.join(['?'] * len(present))), present)
    # 插入：正序
    counts = {}
    for t in present:
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


def _do_merge(c, data, conn):
    """
    按业务键 upsert。冲突时备份"赢"（覆盖本地字段）。
    返回 {table: {'added': N, 'updated': N}}，用户偏好记在 'user_preferences' 下（合并模式跳过）。
    """
    stats = {
        'watchlist_groups': {'added': 0, 'updated': 0},
        'watchlist_stocks': {'added': 0, 'updated': 0},
        'portfolio_accounts': {'added': 0, 'updated': 0},
        'portfolio_positions': {'added': 0, 'updated': 0},
        'user_preferences': {'skipped': 0},
    }

    # ---- 1. 自选分组：按 name upsert ----
    # incoming 的 group_id 必须映射到本地的 group_id（因为自增 id 不可跨机比对）
    gid_map = {}  # incoming_id -> local_id
    for g in (data.get('watchlist_groups') or []):
        name = (g.get('name') or '').strip()
        if not name: continue
        c.execute('SELECT id FROM watchlist_groups WHERE name = ?', (name,))
        row = c.fetchone()
        if row:
            gid_map[g.get('id')] = row['id']
            # 已存在分组：不修改（保留本地排序/创建时间）
        else:
            c.execute('SELECT COALESCE(MAX(sort_order), -1) + 1 FROM watchlist_groups')
            next_order = c.fetchone()[0]
            c.execute('INSERT INTO watchlist_groups (name, sort_order) VALUES (?, ?)',
                      (name, next_order))
            gid_map[g.get('id')] = c.lastrowid
            stats['watchlist_groups']['added'] += 1

    # ---- 2. 自选股：按 (group_name, code) upsert，同键则用备份值覆盖 ----
    for s in (data.get('watchlist_stocks') or []):
        incoming_gid = s.get('group_id')
        local_gid = gid_map.get(incoming_gid)
        if not local_gid:
            continue  # 父分组不存在（不该出现，防御一下）
        code = (s.get('code') or '').strip()
        if not code: continue
        c.execute('SELECT id FROM watchlist_stocks WHERE group_id = ? AND code = ?',
                  (local_gid, code))
        existing = c.fetchone()
        if existing:
            # upsert：备份覆盖本地（sort_order 保留本地）
            c.execute('''UPDATE watchlist_stocks
                         SET name = ?, added_price = ?, remark = ?, added_at = ?
                         WHERE group_id = ? AND code = ?''',
                      (s.get('name') or '', s.get('added_price'),
                       s.get('remark') or '', s.get('added_at'),
                       local_gid, code))
            stats['watchlist_stocks']['updated'] += 1
        else:
            c.execute('SELECT COALESCE(MAX(sort_order), -1) + 1 FROM watchlist_stocks WHERE group_id = ?',
                      (local_gid,))
            next_order = c.fetchone()[0]
            c.execute('''INSERT INTO watchlist_stocks
                         (group_id, code, name, added_price, remark, sort_order, added_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (local_gid, code, s.get('name') or '', s.get('added_price'),
                       s.get('remark') or '', next_order, s.get('added_at')))
            stats['watchlist_stocks']['added'] += 1

    # ---- 3. 持仓账户：按 name upsert ----
    aid_map = {}
    for a in (data.get('portfolio_accounts') or []):
        name = (a.get('name') or '').strip()
        if not name: continue
        c.execute('SELECT id FROM portfolio_accounts WHERE name = ?', (name,))
        row = c.fetchone()
        if row:
            aid_map[a.get('id')] = row['id']
        else:
            c.execute('SELECT COALESCE(MAX(sort_order), -1) + 1 FROM portfolio_accounts')
            next_order = c.fetchone()[0]
            c.execute('INSERT INTO portfolio_accounts (name, sort_order) VALUES (?, ?)',
                      (name, next_order))
            aid_map[a.get('id')] = c.lastrowid
            stats['portfolio_accounts']['added'] += 1

    # ---- 4. 持仓：按 (account_name, code) upsert ----
    for p in (data.get('portfolio_positions') or []):
        local_aid = aid_map.get(p.get('account_id'))
        if not local_aid:
            continue
        code = (p.get('code') or '').strip()
        if not code: continue
        c.execute('SELECT id FROM portfolio_positions WHERE account_id = ? AND code = ?',
                  (local_aid, code))
        existing = c.fetchone()
        if existing:
            c.execute('''UPDATE portfolio_positions
                         SET name = ?, shares = ?, cost_price = ?, remark = ?, added_at = ?
                         WHERE account_id = ? AND code = ?''',
                      (p.get('name') or '', p.get('shares'), p.get('cost_price'),
                       p.get('remark') or '', p.get('added_at'),
                       local_aid, code))
            stats['portfolio_positions']['updated'] += 1
        else:
            c.execute('SELECT COALESCE(MAX(sort_order), -1) + 1 FROM portfolio_positions WHERE account_id = ?',
                      (local_aid,))
            next_order = c.fetchone()[0]
            c.execute('''INSERT INTO portfolio_positions
                         (account_id, code, name, shares, cost_price, remark, sort_order, added_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                      (local_aid, code, p.get('name') or '', p.get('shares'),
                       p.get('cost_price'), p.get('remark') or '', next_order, p.get('added_at')))
            stats['portfolio_positions']['added'] += 1

    # ---- 5. 偏好：合并模式跳过，保留本地 UI 习惯 ----
    stats['user_preferences']['skipped'] = len(data.get('user_preferences') or [])

    conn.commit()
    return stats


# =========== 文件 I/O 包装（给原生文件对话框用）=========== #

def write_backup_to_file(path, sections=None):
    """导出并写入指定路径。sections=None 表示导出所有分区。"""
    data = export_user_data(sections=sections)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # 只统计实际导出的表（未导出的表不出现在 dict 里）
    return {t: len(data.get(t) or []) for t in USER_TABLES if t in data}


def read_backup_from_file(path):
    """读取 JSON 文件返回 dict。不做导入，只解析。"""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def import_from_file(path, mode='replace'):
    """读 + 导入一体。"""
    return import_user_data(read_backup_from_file(path), mode=mode)

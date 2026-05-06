"""
候选池服务 —— 找发车 / 找候选扫出来的票，用户主动 ⭐ 收藏后持久化在这里。

跟 watchlist 概念区分：
  - watchlist = 长期关注的股票（无技术形态约束）
  - candidate_picks = 三维启动扫描器选出的 Stage 1/2 票，专为"持续追踪买点"设计

字段语义（snapshot 永久不变）：
  - save_price:    入选时收盘价（基准参考）
  - break_level:   突破触发位（s1Upper）—— 越过此线即「已突破」
  - golden_price:  黄金买点（试盘后回踩位）—— 当前价进入 ±2% 即「进入买点区」
  - s1_lower:      Stage 1 下沿 —— 跌破即「已失效」（蓄势结构破坏）

当前状态由前端基于 snapshot + 实时价 lazy 计算，不存数据库。
"""
import logging
from datetime import datetime

import db

logger = logging.getLogger(__name__)


def list_picks():
    """列出全部候选池条目，按 saved_at 倒序（最新加入的在前）。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        SELECT id, code, name, saved_at, stage,
               save_price, break_level, golden_price, s1_lower,
               consolidation_bars, source, note
        FROM candidate_picks
        ORDER BY saved_at DESC, id DESC
    ''')
    rows = c.fetchall()
    conn.close()
    return [
        {
            'id':                 r[0],
            'code':               r[1],
            'name':               r[2] or '',
            'saved_at':           r[3],
            'stage':              r[4],
            'save_price':         r[5],
            'break_level':        r[6],
            'golden_price':       r[7],
            's1_lower':           r[8],
            'consolidation_bars': r[9],
            'source':             r[10] or '',
            'note':               r[11] or '',
        }
        for r in rows
    ]


def add_pick(code, name, stage, save_price, break_level,
             golden_price, s1_lower=None, consolidation_bars=None,
             source='三维启动找候选', note=''):
    """
    把扫描器命中的一只票存入候选池。
    UNIQUE(code) 约束 → 同 code 重复保存会**覆盖**旧 snapshot（重新计时观察）。
    返回新/更新行的 id。
    """
    if not code:
        raise ValueError('code 必填')
    code = str(code).strip()
    name = (name or '').strip()
    now = datetime.now().isoformat()

    conn = db.get_db()
    c = conn.cursor()
    # INSERT OR REPLACE 会换 id —— 不希望，所以走 UPSERT 模式
    c.execute('SELECT id FROM candidate_picks WHERE code = ?', (code,))
    existing = c.fetchone()
    if existing:
        c.execute('''
            UPDATE candidate_picks
            SET name = ?, saved_at = ?, stage = ?,
                save_price = ?, break_level = ?, golden_price = ?, s1_lower = ?,
                consolidation_bars = ?, source = ?
            WHERE code = ?
        ''', (name, now, stage,
              save_price, break_level, golden_price, s1_lower,
              consolidation_bars, source, code))
        pick_id = existing[0]
    else:
        c.execute('''
            INSERT INTO candidate_picks
                (code, name, saved_at, stage,
                 save_price, break_level, golden_price, s1_lower,
                 consolidation_bars, source, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (code, name, now, stage,
              save_price, break_level, golden_price, s1_lower,
              consolidation_bars, source, note))
        pick_id = c.lastrowid
    conn.commit()
    conn.close()
    return pick_id


def remove_pick(code):
    """从候选池删一只票。返回是否删除成功（找到记录算成功）。"""
    if not code:
        return False
    conn = db.get_db()
    c = conn.cursor()
    c.execute('DELETE FROM candidate_picks WHERE code = ?', (str(code).strip(),))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def update_note(code, note):
    """改备注。"""
    if not code:
        return False
    conn = db.get_db()
    c = conn.cursor()
    c.execute('UPDATE candidate_picks SET note = ? WHERE code = ?',
              (note or '', str(code).strip()))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def clear_all():
    """清空候选池（危险操作，谨慎调用）。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('DELETE FROM candidate_picks')
    n = c.rowcount
    conn.commit()
    conn.close()
    return n

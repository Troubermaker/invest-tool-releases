"""
交易日志服务（P0 闭环关键）。

跟候选池正交：候选池是 paper trading 观察哨，trades_journal 是**实战账本**。
用途：
  1) 实盘真实胜率追踪 vs 回测预期（验证 76% 真实落地多少）
  2) 仓位 / 风险管理（单笔建议 / 板块集中度警示）
  3) 归因分析（哪条规则贡献多少 pp，哪个 regime 实战表现）
"""
import json
import logging
from datetime import datetime

import db

logger = logging.getLogger(__name__)

_VALID_SIGNAL_SOURCES = {'main_breakout', 'breakout_eve', 'dragon_return', 'limit_up_relay', 'manual'}
_VALID_EXIT_REASONS = {'stop_loss', 'take_profit', 'time_out', 'manual', 'invalid'}


def add_trade(code, name, signal_source, star_level, entry_price,
              position_pct=None, target_price=None, stop_loss=None,
              signal_metadata=None, entry_at=None, notes=''):
    """
    记录买入。返回新行 id。

    Args:
        code:           股票代码（必填）
        name:           股票名
        signal_source:  来源 enum；不在白名单的会归到 'manual'
        star_level:     0-3 星
        entry_price:    买入价（必填）
        position_pct:   仓位占比（如 10 表示 10%）
        target_price:   目标止盈位
        stop_loss:      计划止损位
        signal_metadata: dict，会 json 序列化
        entry_at:       买入时间 ISO，默认 now
        notes:          用户备注
    """
    if not code:
        raise ValueError('code 必填')
    if entry_price is None or entry_price <= 0:
        raise ValueError('entry_price 必填且 > 0')
    if signal_source not in _VALID_SIGNAL_SOURCES:
        logger.warning(f'未知 signal_source={signal_source!r}，归到 manual')
        signal_source = 'manual'
    if star_level is None: star_level = 0
    star_level = max(0, min(4, int(star_level)))

    now_iso = datetime.now().isoformat()
    entry_at = entry_at or now_iso

    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO trades_journal
            (code, name, signal_source, star_level, signal_metadata,
             entry_at, entry_price, position_pct,
             target_price, stop_loss, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        str(code).strip(),
        (name or '').strip(),
        signal_source,
        star_level,
        json.dumps(signal_metadata, ensure_ascii=False) if signal_metadata else None,
        entry_at,
        float(entry_price),
        float(position_pct) if position_pct is not None else None,
        float(target_price) if target_price is not None else None,
        float(stop_loss) if stop_loss is not None else None,
        (notes or '').strip(),
        now_iso,
        now_iso,
    ))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    logger.info(f'add_trade #{trade_id} code={code} signal={signal_source} star={star_level}')
    return trade_id


def close_trade(trade_id, exit_price, exit_reason='manual', exit_at=None, notes=None):
    """
    平仓：记录卖出价、原因。自动算 pnl_pct 和 hold_days。

    Returns: True / False
    """
    if exit_price is None or exit_price <= 0:
        raise ValueError('exit_price 必填且 > 0')
    if exit_reason not in _VALID_EXIT_REASONS:
        exit_reason = 'manual'

    now_iso = datetime.now().isoformat()
    exit_at = exit_at or now_iso

    conn = db.get_db()
    c = conn.cursor()
    # 先读取 entry 信息算 pnl
    c.execute('SELECT entry_price, entry_at FROM trades_journal WHERE id = ?', (trade_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    entry_price, entry_at = row
    pnl_pct = (float(exit_price) - float(entry_price)) / float(entry_price) * 100
    # 持仓天数：粗算（calendar days）
    try:
        d1 = datetime.fromisoformat(entry_at)
        d2 = datetime.fromisoformat(exit_at)
        hold_days = max(0, (d2.date() - d1.date()).days)
    except Exception:
        hold_days = None

    # 拼接 notes
    if notes:
        c.execute('SELECT notes FROM trades_journal WHERE id = ?', (trade_id,))
        old_notes = c.fetchone()[0] or ''
        new_notes = f'{old_notes}\n[{exit_reason}] {notes}'.strip() if old_notes else f'[{exit_reason}] {notes}'
    else:
        new_notes = None

    sql = '''
        UPDATE trades_journal SET
            exit_at      = ?,
            exit_price   = ?,
            exit_reason  = ?,
            pnl_pct      = ?,
            hold_days    = ?,
            updated_at   = ?
    '''
    params = [exit_at, float(exit_price), exit_reason, pnl_pct, hold_days, now_iso]
    if new_notes is not None:
        sql += ', notes = ?'
        params.append(new_notes)
    sql += ' WHERE id = ?'
    params.append(trade_id)

    c.execute(sql, params)
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def update_trade(trade_id, entry_price=None, target_price=None, stop_loss=None,
                 position_pct=None, notes=None):
    """更新可改字段。

    可改：entry_price / target_price / stop_loss / position_pct / notes。
    若 entry_price 改动且该 trade 已平仓（exit_price 非空），自动重算 pnl_pct
    以保持公式 (exit-entry)/entry*100 自洽。
    """
    now_iso = datetime.now().isoformat()
    sets, params = [], []
    if entry_price is not None:
        sets.append('entry_price = ?')
        params.append(float(entry_price))
    if target_price is not None:
        sets.append('target_price = ?')
        params.append(float(target_price))
    if stop_loss is not None:
        sets.append('stop_loss = ?')
        params.append(float(stop_loss))
    if position_pct is not None:
        sets.append('position_pct = ?')
        params.append(float(position_pct))
    if notes is not None:
        sets.append('notes = ?')
        params.append((notes or '').strip())
    if not sets:
        return False
    sets.append('updated_at = ?')
    params.append(now_iso)
    params.append(trade_id)

    conn = db.get_db()
    c = conn.cursor()
    c.execute(f'UPDATE trades_journal SET {", ".join(sets)} WHERE id = ?', params)
    ok = c.rowcount > 0
    # 已平仓的票如果改了 entry_price，重算 pnl_pct 保持公式自洽
    if ok and entry_price is not None:
        c.execute('SELECT entry_price, exit_price FROM trades_journal WHERE id = ?', (trade_id,))
        row = c.fetchone()
        if row and row['exit_price'] is not None and row['entry_price'] not in (None, 0):
            new_pnl = (float(row['exit_price']) - float(row['entry_price'])) / float(row['entry_price']) * 100
            c.execute('UPDATE trades_journal SET pnl_pct = ? WHERE id = ?', (new_pnl, trade_id))
    conn.commit()
    conn.close()
    return ok


def delete_trade(trade_id):
    """删除日志（误录可删）。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('DELETE FROM trades_journal WHERE id = ?', (trade_id,))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def list_trades(status='all', signal_source=None, limit=200):
    """
    列出 trades。

    Args:
        status:        'all' | 'open'（未平仓）| 'closed'（已平仓）
        signal_source: 过滤来源
        limit:         上限
    """
    conn = db.get_db()
    c = conn.cursor()
    where_clauses = []
    params = []
    if status == 'open':
        where_clauses.append('exit_at IS NULL')
    elif status == 'closed':
        where_clauses.append('exit_at IS NOT NULL')
    if signal_source:
        where_clauses.append('signal_source = ?')
        params.append(signal_source)
    where_sql = ('WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''
    params.append(limit)

    c.execute(f'''
        SELECT id, code, name, signal_source, star_level, signal_metadata,
               entry_at, entry_price, position_pct,
               target_price, stop_loss,
               exit_at, exit_price, exit_reason,
               pnl_pct, hold_days, notes
        FROM trades_journal
        {where_sql}
        ORDER BY entry_at DESC
        LIMIT ?
    ''', params)
    rows = c.fetchall()
    conn.close()
    return [
        {
            'id':              r[0],
            'code':            r[1],
            'name':            r[2] or '',
            'signal_source':   r[3],
            'star_level':      r[4],
            'signal_metadata': json.loads(r[5]) if r[5] else None,
            'entry_at':        r[6],
            'entry_price':     r[7],
            'position_pct':    r[8],
            'target_price':    r[9],
            'stop_loss':       r[10],
            'exit_at':         r[11],
            'exit_price':      r[12],
            'exit_reason':     r[13],
            'pnl_pct':         r[14],
            'hold_days':       r[15],
            'notes':           r[16] or '',
        }
        for r in rows
    ]


def get_summary(period_days=30):
    """
    period_days 内已平仓 trades 的胜率 / 平均盈亏 / 最大回撤。
    """
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=int(period_days))).isoformat()
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        SELECT signal_source, star_level, pnl_pct, hold_days
        FROM trades_journal
        WHERE exit_at IS NOT NULL AND exit_at >= ?
    ''', (cutoff,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return {
            'period_days': period_days,
            'total': 0,
            'overall': None,
            'by_source': {},
            'by_star': {},
        }

    def summarize(arr):
        if not arr: return None
        pnls = [r[2] for r in arr if r[2] is not None]
        if not pnls: return None
        wins = sum(1 for p in pnls if p > 0)
        return {
            'count':     len(pnls),
            'win_rate':  wins / len(pnls),
            'avg_pnl':   sum(pnls) / len(pnls),
            'median_pnl': sorted(pnls)[len(pnls) // 2],
            'max_win':   max(pnls),
            'max_loss':  min(pnls),
            'avg_hold':  sum(r[3] for r in arr if r[3] is not None) / max(1, sum(1 for r in arr if r[3] is not None)),
        }

    by_source = {}
    sources = set(r[0] for r in rows)
    for s in sources:
        by_source[s] = summarize([r for r in rows if r[0] == s])

    by_star = {}
    for star in [4, 3, 2, 1, 0]:
        sub = [r for r in rows if r[1] == star]
        if sub:
            by_star[f'star_{star}'] = summarize(sub)

    # 预期胜率（基于跨段 holdout 验证；用户实测应在 ± 10pp 内才算"模型还有效"）
    expected = {
        'star_4': 0.78,   # confirm + LHB 双重确认（稀缺但最强）
        'star_3': 0.74,   # confirm OR LHB（实测 holdout 74.4%）
        'star_2': 0.66,   # medium / 4 段平均 76% 但只用 medium 单独
        'star_1': 0.55,   # 辅助信号（接近 baseline）
        'star_0': 0.50,   # 无信号支撑
    }

    return {
        'period_days': period_days,
        'total':       len(rows),
        'overall':     summarize(rows),
        'by_source':   by_source,
        'by_star':     by_star,
        'expected_win_rate': expected,
    }


def suggest_position(star_level, total_open_positions, sector_open_pct):
    """
    仓位建议算法（保守规则）：
      ⭐⭐⭐ → 15% 单笔
      ⭐⭐  → 10%
      ⭐    → 5%
      普通  → 0%（不建议买）

    硬约束：
      - 单笔最大 15%
      - 同期持仓上限 5 只
      - 单板块仓位上限 30%

    Args:
        star_level:               信号星级 0-3
        total_open_positions:     当前未平仓数
        sector_open_pct:          该板块已持仓 %（0-100）

    Returns:
        { suggest_pct, max_pct, warnings: [...] }
    """
    base_pct = {3: 15, 2: 10, 1: 5, 0: 0}.get(int(star_level), 0)
    warnings = []
    if total_open_positions >= 5:
        warnings.append('当前持仓 5 只已满，建议等仓位释放再加')
        base_pct = 0
    if sector_open_pct >= 25:
        warnings.append(f'该板块已持仓 {sector_open_pct:.0f}%，接近 30% 上限')
        base_pct = min(base_pct, max(0, 30 - sector_open_pct))
    return {
        'suggest_pct': base_pct,
        'max_pct':     15,
        'warnings':    warnings,
    }

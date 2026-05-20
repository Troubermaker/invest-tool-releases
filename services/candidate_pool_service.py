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
               consolidation_bars, source, note,
               peak_gain_since_save, formation_state, last_refreshed_at,
               secondary_entry_at, secondary_entry_price,
               breakout_at, ml_score,
               breakout_confirm, lhb_in_window, lhb_count, lhb_net_buy, star_level
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
            # Phase 5 追踪字段（None 表示未刷新过）
            'peak_gain_since_save': r[12],
            'formation_state':      r[13],
            'last_refreshed_at':    r[14],
            # Phase 6 二次买点（反包出现的时间和价位；None 表示未出现 / 未刷新）
            'secondary_entry_at':    r[15],
            'secondary_entry_price': r[16],
            # Phase 7 突破日跟踪（detector 跑出的最新 stage 3 突破 K 时间）
            'breakout_at':           r[17],
            # Phase 3 Step 2 ML 预测分数（0-1；None = 未刷新过 / 非突破态）
            'ml_score':              r[18],
            # P2 + Week 2 Day 1：综合信号
            'breakout_confirm':      r[19],
            'lhb_in_window':         r[20],
            'lhb_count':             r[21],
            'lhb_net_buy':           r[22],
            'star_level':            r[23],
        }
        for r in rows
    ]


def _merge_source(old_source, new_source):
    """合并多个扫描器来源（去重 + 保序）。
    例：old='龙回头' + new='连板游资' → '龙回头+连板游资'
        old='龙回头+连板游资' + new='龙回头' → '龙回头+连板游资'（不重复）
    """
    new_source = (new_source or '').strip()
    if not new_source:
        return (old_source or '').strip()
    parts = [s.strip() for s in (old_source or '').split('+') if s.strip()]
    if new_source not in parts:
        parts.append(new_source)
    return '+'.join(parts)


def add_pick(code, name, stage, save_price, break_level,
             golden_price, s1_lower=None, consolidation_bars=None,
             source='三维启动找候选', note=''):
    """
    把扫描器命中的一只票存入候选池。
    UNIQUE(code) 约束 → 同 code 重复保存：
      - source 合并（多 detector 命中的票会变成 '龙回头+连板游资' 这种）
      - 其它 snapshot 字段被新值覆盖（重新计时观察）
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
    c.execute('SELECT id, source FROM candidate_picks WHERE code = ?', (code,))
    existing = c.fetchone()
    if existing:
        merged_source = _merge_source(existing[1], source)
        c.execute('''
            UPDATE candidate_picks
            SET name = ?, saved_at = ?, stage = ?,
                save_price = ?, break_level = ?, golden_price = ?, s1_lower = ?,
                consolidation_bars = ?, source = ?
            WHERE code = ?
        ''', (name, now, stage,
              save_price, break_level, golden_price, s1_lower,
              consolidation_bars, merged_source, code))
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


# ============== Phase 5 动态追踪字段刷新 ==============
#
# 设计：前端 useCandidatePoolRefresher 跑完 detector 后批量调 bulk_update_tracking 写回。
# 后端只做存储 + 基础校验，detector 逻辑全部在前端（单一来源避免漂移）。

_VALID_FORMATION_STATES = {
    'consolidating',  # Stage 1 蓄势中
    'tested',         # Stage 2 试盘后等突破
    'breakout',       # Stage 3 刚突破（fresh）
    'rally',          # 主升中（已突破一段时间，趋势仍有效）
    'exhausted',      # 衰竭警示（detectRallyExhaustion 'reduce'/'exit'）
    'invalid',        # 形态作废（detectRallyExhaustion 'invalid'）
    'stretched',      # 横盘跳空型主升（detectStretchedRally —— 跟三维启动互补，
                      # 抓"长横盘 → 直接跳空"型，不要求 S2 试盘）
    'unknown',        # detector 跑失败 / 数据不足
}


def update_tracking(code, peak_gain_since_save=None, formation_state=None,
                    secondary_entry_at=None, secondary_entry_price=None,
                    breakout_at=None,
                    last_refreshed_at=None):
    """
    更新单条候选池的动态追踪字段。
    传 None 的字段保持原值不变（COALESCE 语义）。

    secondary_entry_at / secondary_entry_price 特殊：传 '' 字符串显式清空（detector 判定
    二次买点已失效），其它情况按 None 跳过更新。
    """
    if not code:
        return False
    code = str(code).strip()

    if formation_state is not None and formation_state not in _VALID_FORMATION_STATES:
        logger.warning(f'未知 formation_state={formation_state!r}，仍写入但建议核查')

    if last_refreshed_at is None:
        last_refreshed_at = datetime.now().isoformat()

    # 显式清空（"")：转为 SQL NULL；不传 / None：保持原值
    se_at  = None if secondary_entry_at  in (None, '') else secondary_entry_at
    se_pri = None if secondary_entry_price in (None,) else secondary_entry_price
    se_clear = secondary_entry_at == ''

    # breakout_at 同 secondary：'' 显式清空，None 跳过
    bo_at = None if breakout_at in (None, '') else breakout_at
    bo_clear = breakout_at == ''

    conn = db.get_db()
    c = conn.cursor()
    # 用动态 SQL 处理"显式清空"vs"COALESCE 保留"两种模式
    se_clause = 'secondary_entry_at = NULL, secondary_entry_price = NULL' if se_clear \
                else 'secondary_entry_at = COALESCE(?, secondary_entry_at), ' \
                     'secondary_entry_price = COALESCE(?, secondary_entry_price)'
    bo_clause = 'breakout_at = NULL' if bo_clear else 'breakout_at = COALESCE(?, breakout_at)'

    sql = f'''
        UPDATE candidate_picks SET
            peak_gain_since_save  = COALESCE(?, peak_gain_since_save),
            formation_state       = COALESCE(?, formation_state),
            {se_clause},
            {bo_clause},
            last_refreshed_at     = ?
        WHERE code = ?
    '''
    params = [peak_gain_since_save, formation_state]
    if not se_clear:
        params += [se_at, se_pri]
    if not bo_clear:
        params.append(bo_at)
    params += [last_refreshed_at, code]

    c.execute(sql, params)
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def bulk_update_tracking(payloads):
    """
    批量更新追踪字段（用于前端"刷新候选池"一次性写回所有结果）。

    payloads: [{
        'code': '600519',
        'peak_gain_since_save': 12.5,            # optional
        'formation_state': 'breakout',            # optional
        'secondary_entry_at': '2026-04-15',       # optional, '' 显式清空（detector 判定失效）
        'secondary_entry_price': 23.45,           # optional
        'breakout_at': '2026-04-10',              # optional, '' 显式清空（不是 stage 3）
        'ml_score': 0.47,                         # optional, None 跳过 / 'clear' 显式清空（非突破态时）
    }, ...]

    所有记录共用同一个 last_refreshed_at（调用时刻）。
    返回 { updated: N, skipped: M, refreshed_at }
    """
    if not isinstance(payloads, list) or not payloads:
        return {'updated': 0, 'skipped': 0}

    now_iso = datetime.now().isoformat()
    conn = db.get_db()
    c = conn.cursor()
    updated, skipped = 0, 0
    for p in payloads:
        code = (p.get('code') or '').strip()
        if not code:
            skipped += 1
            continue
        fs = p.get('formation_state')
        if fs is not None and fs not in _VALID_FORMATION_STATES:
            logger.warning(f'未知 formation_state={fs!r} for code={code}')

        se_at_raw = p.get('secondary_entry_at')
        se_pri    = p.get('secondary_entry_price')
        se_clear  = se_at_raw == ''
        se_at     = None if se_at_raw in (None, '') else se_at_raw

        bo_at_raw = p.get('breakout_at')
        bo_clear  = bo_at_raw == ''
        bo_at     = None if bo_at_raw in (None, '') else bo_at_raw

        # Phase 3 Step 2 ML 分数：'clear' / None / float
        ml_raw    = p.get('ml_score')
        ml_clear  = ml_raw == 'clear'
        ml_val    = None if (ml_raw is None or ml_clear) else float(ml_raw)

        # P2 + Week 2 Day 1：综合信号（'clear' / None 跳过 / 值）
        def _coerce_or_clear(v, caster=None):
            """返回 (clear_bool, value_or_none, skip_bool)。"""
            if v == 'clear': return True, None, False
            if v is None:    return False, None, True
            return False, (caster(v) if caster else v), False

        bc_clear, bc_val, bc_skip = _coerce_or_clear(p.get('breakout_confirm'))
        liw_clear, liw_val, liw_skip = _coerce_or_clear(p.get('lhb_in_window'), int)
        lc_clear, lc_val, lc_skip = _coerce_or_clear(p.get('lhb_count'), int)
        lnb_clear, lnb_val, lnb_skip = _coerce_or_clear(p.get('lhb_net_buy'), float)
        st_clear, st_val, st_skip = _coerce_or_clear(p.get('star_level'), int)

        # 动态 SQL
        se_clause = 'secondary_entry_at = NULL, secondary_entry_price = NULL' if se_clear \
                    else 'secondary_entry_at = COALESCE(?, secondary_entry_at), ' \
                         'secondary_entry_price = COALESCE(?, secondary_entry_price)'
        bo_clause = 'breakout_at = NULL' if bo_clear else 'breakout_at = COALESCE(?, breakout_at)'
        ml_clause = 'ml_score = NULL' if ml_clear else 'ml_score = COALESCE(?, ml_score)'
        bc_clause = 'breakout_confirm = NULL' if bc_clear else (None if bc_skip else 'breakout_confirm = ?')
        liw_clause = 'lhb_in_window = NULL' if liw_clear else (None if liw_skip else 'lhb_in_window = ?')
        lc_clause  = 'lhb_count = NULL'     if lc_clear  else (None if lc_skip  else 'lhb_count = ?')
        lnb_clause = 'lhb_net_buy = NULL'   if lnb_clear else (None if lnb_skip else 'lhb_net_buy = ?')
        st_clause  = 'star_level = NULL'    if st_clear  else (None if st_skip  else 'star_level = ?')

        extra_clauses = [c for c in [bc_clause, liw_clause, lc_clause, lnb_clause, st_clause] if c]
        extra_sql = (', ' + ', '.join(extra_clauses)) if extra_clauses else ''

        sql = f'''
            UPDATE candidate_picks SET
                peak_gain_since_save  = COALESCE(?, peak_gain_since_save),
                formation_state       = COALESCE(?, formation_state),
                {se_clause},
                {bo_clause},
                {ml_clause}{extra_sql},
                last_refreshed_at     = ?
            WHERE code = ?
        '''
        params = [p.get('peak_gain_since_save'), fs]
        if not se_clear:
            params += [se_at, se_pri]
        if not bo_clear:
            params.append(bo_at)
        if not ml_clear:
            params.append(ml_val)
        # 按 clause 顺序追加非 clear / 非 skip 的值
        if not bc_clear  and not bc_skip:  params.append(bc_val)
        if not liw_clear and not liw_skip: params.append(liw_val)
        if not lc_clear  and not lc_skip:  params.append(lc_val)
        if not lnb_clear and not lnb_skip: params.append(lnb_val)
        if not st_clear  and not st_skip:  params.append(st_val)
        params += [now_iso, code]

        c.execute(sql, params)
        if c.rowcount > 0:
            updated += 1
        else:
            skipped += 1
    conn.commit()
    conn.close()
    return {'updated': updated, 'skipped': skipped, 'refreshed_at': now_iso}

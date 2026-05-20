"""
龙虎榜数据服务（P2 阶段：A 股 +15-20pp 胜率的关键数据维度）。

数据源：东方财富 (datacenter-web.eastmoney.com)
缓存策略：写入 lhb_records 表（按 (code, date) 主键去重），按需增量拉取。

核心 API：
  refresh_lhb_data(days=90)     按天滚动拉取最近 N 天，已有的跳过
  get_stock_lhb_history(code)   单股最近 N 天 LHB 记录
  get_lhb_features(code, date)  给定 code + 事件日期，返回 4 维 LHB 特征供 ML 用
"""
import logging
import sqlite3
from datetime import datetime, timedelta

import db
from api_endpoints import eastmoney

logger = logging.getLogger(__name__)


def _normalize_date(d):
    """统一日期为 'YYYY-MM-DD'。"""
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, datetime):
        return d.strftime('%Y-%m-%d')
    return str(d)[:10]


def refresh_lhb_data(days=90, force=False):
    """
    拉取最近 N 天龙虎榜数据，写入 lhb_records 表（INSERT OR REPLACE 去重）。

    Args:
        days:  回看天数（默认 90 天，覆盖 ML 训练的 30 天窗口 + 余量）
        force: 跳过"已存在某天则跳过"的判断，强制重拉

    Returns:
        { added: N, total_today: M, days: D }
    """
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # 已有的日期（避免重复拉）
    conn = db.get_db()
    c = conn.cursor()
    existing_dates = set()
    if not force:
        c.execute('SELECT DISTINCT date FROM lhb_records WHERE date >= ? AND date <= ?', (start_date, end_date))
        existing_dates = {r[0] for r in c.fetchall()}

    # 拉取（一次拉完整 90 天，分页防数据量超 500）
    all_rows = []
    page = 1
    while True:
        try:
            resp = eastmoney.raw_em_lhb_daily_list(start_date, end_date, page_size=500, page_number=page)
        except Exception as e:
            logger.warning(f'LHB 拉取失败 page={page}: {e}')
            break
        if not resp or not resp.get('result'):
            break
        page_data = resp['result'].get('data') or []
        if not page_data:
            break
        all_rows.extend(page_data)
        # 判断是否到底
        total = resp['result'].get('count') or 0
        if page * 500 >= total: break
        page += 1
        if page > 20: break  # 安全闸

    logger.info(f'LHB 原始拉取 {len(all_rows)} 条记录')

    # 写入（force=False 时跳过已存在日期，但每天数据可能跨多页 → 整段日期不同时仍写）
    added = 0
    for row in all_rows:
        code = (row.get('SECURITY_CODE') or '').strip()
        if not code: continue
        date_raw = row.get('TRADE_DATE') or ''
        date_str = _normalize_date(date_raw)
        if not force and date_str in existing_dates:
            # 该天已经在表里，但这条 (code, date) 可能新；INSERT OR REPLACE 处理
            pass
        try:
            c.execute('''
                INSERT OR REPLACE INTO lhb_records
                    (code, date, name, net_buy, buy_amt, sell_amt, deal_amt,
                     explain, change_rate, close_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                code,
                date_str,
                (row.get('SECURITY_NAME_ABBR') or '').strip(),
                float(row.get('BILLBOARD_NET_AMT') or 0),
                float(row.get('BILLBOARD_BUY_AMT') or 0),
                float(row.get('BILLBOARD_SELL_AMT') or 0),
                float(row.get('BILLBOARD_DEAL_AMT') or 0),
                row.get('EXPLAIN') or '',
                float(row.get('CHANGE_RATE') or 0),
                float(row.get('CLOSE_PRICE') or 0),
            ))
            added += 1
        except Exception as e:
            logger.warning(f'LHB 写入失败 {code} {date_str}: {e}')

    conn.commit()
    # 统计今日记录数
    c.execute('SELECT COUNT(*) FROM lhb_records')
    total = c.fetchone()[0]
    conn.close()

    logger.info(f'LHB refresh 完成：写入 {added} 条，表内共 {total} 条')
    return {
        'added':       added,
        'total_table': total,
        'days':        days,
        'start_date':  start_date,
        'end_date':    end_date,
    }


def get_stock_lhb_history(code, lookback_days=90):
    """单只票最近 N 天的所有 LHB 记录。"""
    if not code: return []
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        SELECT date, name, net_buy, buy_amt, sell_amt, deal_amt, explain, change_rate, close_price
        FROM lhb_records
        WHERE code = ? AND date >= ?
        ORDER BY date DESC
    ''', (code, cutoff))
    rows = c.fetchall()
    conn.close()
    return [
        {
            'date': r[0], 'name': r[1],
            'net_buy': r[2], 'buy_amt': r[3], 'sell_amt': r[4], 'deal_amt': r[5],
            'explain': r[6], 'change_rate': r[7], 'close_price': r[8],
        }
        for r in rows
    ]


def get_lhb_features(code, event_date=None, window_days=30):
    """
    给定 (code, event_date)，返回 4 维 LHB 特征供 ML 用。
    event_date 默认 today；ML 训练时传历史 trade 的 entryTime。

    Returns:
        {
            lhb_in_window:    int 0/1     —— 窗口内是否上榜
            lhb_count:        int         —— 窗口内上榜次数
            lhb_net_buy_sum:  float       —— 窗口内净买额总和（元）
            days_since_last_lhb: int|None —— 距上次上榜天数（>window 返 None）
        }
    """
    if not code:
        return {'lhb_in_window': 0, 'lhb_count': 0, 'lhb_net_buy_sum': 0.0, 'days_since_last_lhb': None}

    if event_date is None:
        event_date = datetime.now().strftime('%Y-%m-%d')
    event_date = _normalize_date(event_date)

    try:
        event_dt = datetime.strptime(event_date, '%Y-%m-%d')
    except ValueError:
        return {'lhb_in_window': 0, 'lhb_count': 0, 'lhb_net_buy_sum': 0.0, 'days_since_last_lhb': None}

    start_dt = event_dt - timedelta(days=window_days)
    start_str = start_dt.strftime('%Y-%m-%d')

    conn = db.get_db()
    c = conn.cursor()
    # 关键：date <= event_date 避免未来泄漏
    c.execute('''
        SELECT date, net_buy
        FROM lhb_records
        WHERE code = ? AND date >= ? AND date <= ?
        ORDER BY date DESC
    ''', (code, start_str, event_date))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return {'lhb_in_window': 0, 'lhb_count': 0, 'lhb_net_buy_sum': 0.0, 'days_since_last_lhb': None}

    count = len(rows)
    net_sum = sum(r[1] or 0 for r in rows)
    # 距上次最近一条
    try:
        last_dt = datetime.strptime(rows[0][0], '%Y-%m-%d')
        days_since = (event_dt - last_dt).days
    except ValueError:
        days_since = None

    return {
        'lhb_in_window':       1,
        'lhb_count':           count,
        'lhb_net_buy_sum':     net_sum,
        'days_since_last_lhb': days_since,
    }


def get_lhb_batch_features(codes, event_date=None, window_days=30):
    """
    批量版本：给定 codes list + 单一 event_date，返回 { code: features }。
    用于 ML 批量训练时一次查询多只票。
    """
    if not isinstance(codes, list) or not codes:
        return {}
    if event_date is None:
        event_date = datetime.now().strftime('%Y-%m-%d')
    event_date = _normalize_date(event_date)

    try:
        event_dt = datetime.strptime(event_date, '%Y-%m-%d')
    except ValueError:
        return {c: {'lhb_in_window': 0, 'lhb_count': 0, 'lhb_net_buy_sum': 0.0, 'days_since_last_lhb': None} for c in codes}

    start_str = (event_dt - timedelta(days=window_days)).strftime('%Y-%m-%d')

    conn = db.get_db()
    c = conn.cursor()
    out = {}
    BATCH = 500
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i + BATCH]
        placeholders = ','.join('?' * len(batch))
        c.execute(f'''
            SELECT code, date, net_buy
            FROM lhb_records
            WHERE code IN ({placeholders}) AND date >= ? AND date <= ?
            ORDER BY date DESC
        ''', list(batch) + [start_str, event_date])
        agg = {}
        for code, date_str, net_buy in c.fetchall():
            entry = agg.setdefault(code, {'count': 0, 'net_sum': 0.0, 'last_date': None})
            entry['count'] += 1
            entry['net_sum'] += (net_buy or 0)
            if entry['last_date'] is None or date_str > entry['last_date']:
                entry['last_date'] = date_str
        for code in batch:
            a = agg.get(code)
            if not a:
                out[code] = {'lhb_in_window': 0, 'lhb_count': 0, 'lhb_net_buy_sum': 0.0, 'days_since_last_lhb': None}
            else:
                try:
                    days_since = (event_dt - datetime.strptime(a['last_date'], '%Y-%m-%d')).days
                except ValueError:
                    days_since = None
                out[code] = {
                    'lhb_in_window':       1,
                    'lhb_count':           a['count'],
                    'lhb_net_buy_sum':     a['net_sum'],
                    'days_since_last_lhb': days_since,
                }
    conn.close()
    return out


def stats():
    """诊断：LHB 表统计信息。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('SELECT COUNT(*), MIN(date), MAX(date) FROM lhb_records')
    total, min_d, max_d = c.fetchone()
    c.execute('SELECT date, COUNT(*) FROM lhb_records GROUP BY date ORDER BY date DESC LIMIT 10')
    recent = c.fetchall()
    conn.close()
    return {
        'total_records':   total or 0,
        'date_range':      [min_d, max_d],
        'recent_by_date':  recent,
    }

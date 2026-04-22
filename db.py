import sqlite3
import os
import json
from datetime import datetime, time as dtime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'invest_data.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    # 市场行情缓存表
    c.execute('''
        CREATE TABLE IF NOT EXISTS market_cache (
            key TEXT PRIMARY KEY,
            data TEXT,
            updated_at TIMESTAMP
        )
    ''')
    # 自选股表
    c.execute('''
        CREATE TABLE IF NOT EXISTS watchlist (
            code TEXT PRIMARY KEY,
            name TEXT,
            added_at TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def set_cache(key, data):
    """写入缓存。updated_at 自动记录为当前时间。"""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO market_cache (key, data, updated_at)
        VALUES (?, ?, ?)
    ''', (key, json.dumps(data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_cache(key, max_age_seconds=None):
    """
    读取缓存。
    - 不传 max_age_seconds：返回 (data, updated_at_str) 或 (None, None)
    - 传 max_age_seconds：超过该秒数自动判断为过期，返回 (None, None)
    """
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT data, updated_at FROM market_cache WHERE key = ?', (key,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None, None

    data = json.loads(row['data'])
    updated_at_str = row['updated_at']

    if max_age_seconds is not None:
        try:
            updated_at = datetime.fromisoformat(updated_at_str)
            if (datetime.now() - updated_at).total_seconds() > max_age_seconds:
                return None, None
        except Exception:
            return None, None

    return data, updated_at_str

def clear_cache(key=None):
    """清除缓存。传 key 只清该条，不传则清全部。"""
    conn = get_db()
    c = conn.cursor()
    if key:
        c.execute('DELETE FROM market_cache WHERE key = ?', (key,))
    else:
        c.execute('DELETE FROM market_cache')
    conn.commit()
    conn.close()

def is_market_cache_stale(updated_at_str):
    """
    行情数据专用的时效判断（保留原 fetcher.is_cache_stale 的语义）：
    - 跨天 → 一定失效
    - 交易时段（9:30-11:35 / 13:00-15:00）→ 超过 1 分钟失效
    - 盘后或周末 → 超过 1 小时失效
    """
    if not updated_at_str:
        return True
    try:
        updated_at = datetime.fromisoformat(updated_at_str)
        now = datetime.now()

        if updated_at.date() < now.date():
            return True

        cur = now.time()
        is_trading = (dtime(9, 30) <= cur <= dtime(11, 35)) or \
                     (dtime(13, 0) <= cur <= dtime(15, 0))

        delta = (now - updated_at).total_seconds()
        if is_trading:
            return delta > 60
        else:
            return delta > 3600
    except Exception:
        return True

init_db()

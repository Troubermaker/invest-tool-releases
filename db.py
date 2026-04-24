import sqlite3
import os
import json
from datetime import datetime, time as dtime, timedelta

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
    # 旧单表自选（保留兼容；新代码用下面的 watchlist_groups/watchlist_stocks）
    c.execute('''
        CREATE TABLE IF NOT EXISTS watchlist (
            code TEXT PRIMARY KEY,
            name TEXT,
            added_at TIMESTAMP
        )
    ''')
    # 自选分组
    c.execute('''
        CREATE TABLE IF NOT EXISTS watchlist_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 自选分组下的股票
    c.execute('''
        CREATE TABLE IF NOT EXISTS watchlist_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            added_price REAL,
            remark TEXT DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(group_id, code),
            FOREIGN KEY (group_id) REFERENCES watchlist_groups(id) ON DELETE CASCADE
        )
    ''')
    # 迁移旧表：给已存在的 watchlist_stocks 表补 added_price / remark 字段
    for col, decl in [('added_price', 'REAL'), ('remark', "TEXT DEFAULT ''")]:
        try:
            c.execute(f'ALTER TABLE watchlist_stocks ADD COLUMN {col} {decl}')
        except Exception:
            pass  # 已存在则忽略
    # 用户偏好键值存储（列顺序、主题等）
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_preferences (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 持仓账户（多账户分组，和自选分组同构）
    c.execute('''
        CREATE TABLE IF NOT EXISTS portfolio_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 持仓记录：快照式，仅存 持股/成本价，市值/盈亏由前端配合实时行情算
    c.execute('''
        CREATE TABLE IF NOT EXISTS portfolio_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            shares INTEGER NOT NULL,
            cost_price REAL NOT NULL,
            remark TEXT DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id, code),
            FOREIGN KEY (account_id) REFERENCES portfolio_accounts(id) ON DELETE CASCADE
        )
    ''')
    # 开启外键约束（sqlite 默认关闭）
    c.execute('PRAGMA foreign_keys = ON')
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

# ========= A 股交易日历 =========
# 用 chinese_calendar 判断是否交易日。它是纯 Python 离线库，不带 V8 引擎，
# 不会和 pywebview 的 Chromium/V8 冲突（akshare 因为依赖 py_mini_racer 会崩进程）。
# chinese_calendar.is_workday 基于国务院公告的法定节假日 + 调休规则，
# A 股交易日基本等同于"法定工作日"（节假日休市，调休补班日也开市）。

def is_trading_day(d):
    """
    判断某 date 是不是 A 股交易日。
    规则：
      - 周末 → 先默认非交易日
      - chinese_calendar 识别的法定节假日 → 非交易日
      - chinese_calendar 识别的调休补班日（周末但算工作日）→ 交易日
      - 失败时降级到 weekday 判断
    """
    try:
        import chinese_calendar as cc
        return cc.is_workday(d)
    except Exception:
        # 库未安装 / 日期超出支持范围（chinese_calendar 通常支持当前年份 + 下一年）
        return d.weekday() < 5


def _current_trading_day(t):
    """
    把任意时刻归属到一个"交易日"。
    规则：
      - 9:30 开盘前 → 回退到上一个交易日（今天算在昨天的交易日里）
      - 开盘后 → 今天（如果今天本身不是交易日则继续回退）
      - 周末 / 节假日 / 调休不开市日 → 回退到最近的交易日
    """
    time_of_day = t.time()
    d = t.date()
    if time_of_day < dtime(9, 30):
        d -= timedelta(days=1)
    # 最多回退两周避免死循环（春节长假最长 10 天左右）
    safety = 14
    while safety > 0 and not is_trading_day(d):
        d -= timedelta(days=1)
        safety -= 1
    return d


def is_market_cache_stale(updated_at_str, trading_ttl=60, offhours_ttl=24*3600):
    """
    行情数据专用的时效判断。支持按数据源差异化 TTL。

    判定顺序：
      1) 跨交易日 → 立即失效（保证每天开盘第一次访问必然拉新）
      2) 同一交易日、交易时段内 → trading_ttl（默认 60s）
      3) 同一交易日、非交易时段 → offhours_ttl（默认 24h，主要是兜底）

    各服务按自身刷新节奏传入适当的 trading_ttl：
        - 指数/板块/市场情绪：15s
        - 连板天梯：30s
        - sparkline 分时：保留默认 60s
        - 自选持仓 quotes：10s
    """
    if not updated_at_str:
        return True
    try:
        updated_at = datetime.fromisoformat(updated_at_str)
        now = datetime.now()

        # 规则 1: 跨交易日 → 必然失效
        if _current_trading_day(updated_at) != _current_trading_day(now):
            return True

        # 规则 2: 同一交易日内的"盘中 → 收盘"转换
        # 缓存写于本交易日 15:00 之前，而现在已过 15:00 → 强制刷新一次拿收盘价
        # 否则 16:00 打开 app 会一直看着今天 10:00 的盘中价
        trading_day_of_cache = _current_trading_day(updated_at)
        session_close = datetime.combine(trading_day_of_cache, dtime(15, 0))
        if updated_at < session_close <= now:
            return True

        cur = now.time()
        # A 股交易日 + 交易时段双重校验，节假日 / 调休日不算交易时段
        is_trading = is_trading_day(now.date()) and (
            (dtime(9, 30) <= cur <= dtime(11, 35)) or
            (dtime(13, 0) <= cur <= dtime(15, 0))
        )
        delta = (now - updated_at).total_seconds()
        if is_trading:
            return delta > trading_ttl
        return delta > offhours_ttl
    except Exception:
        return True

init_db()

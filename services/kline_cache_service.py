"""
TDX K线持久化缓存（仅给回测 / 全市场扫描用，每日刷新）。

为什么用独立 SQLite 文件而不是塞到 invest_data.db：
  - 主板 3000 只 × 800 bar JSON ≈ 200 MB，会让 invest_data.db 巨型化
  - 用户数据库可能被备份 / 迁移，缓存数据不该跟着走（删了能秒重建）
  - 缓存可以独立 VACUUM / 重置，不影响用户数据

缓存策略：
  - key = (code, timeframe)
  - value = (fetched_date, klines_json)
  - 命中条件：fetched_date == 今日 → 用缓存
  - 跨日自动失效：明天调用同一只票会重新从 TDX 拉取并 INSERT OR REPLACE
"""
import datetime
import json
import logging
import os
import sqlite3
import threading

import db   # 复用 db.py 的数据目录解析逻辑

logger = logging.getLogger(__name__)

_CACHE_DB_PATH = os.path.join(os.path.dirname(db.DB_PATH), 'kline_cache.db')
_init_lock = threading.Lock()
_initialized = False


def _ensure_init():
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        conn = sqlite3.connect(_CACHE_DB_PATH)
        try:
            # WAL：让回测批量并发写不会卡 / 损坏
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS kline_cache (
                    code         TEXT NOT NULL,
                    timeframe    TEXT NOT NULL,
                    fetched_date TEXT NOT NULL,    -- YYYY-MM-DD（用于失效判定）
                    fetched_at   TEXT,              -- Phase 7: ISO 时间戳，识别盘中 partial K
                    klines_json  TEXT NOT NULL,
                    PRIMARY KEY (code, timeframe)
                )
            ''')
            # Phase 7 迁移：老库可能没 fetched_at 列
            try:
                conn.execute('ALTER TABLE kline_cache ADD COLUMN fetched_at TEXT')
            except Exception:
                pass
            conn.execute('CREATE INDEX IF NOT EXISTS idx_kline_cache_date ON kline_cache(fetched_date)')
            conn.commit()
        finally:
            conn.close()
        _initialized = True


def lookup(code, timeframe):
    """返回 (fetched_date, klines_list) 或 (None, None)。老 caller 兼容。"""
    fetched_date, _, klines = lookup_full(code, timeframe)
    return fetched_date, klines


def lookup_full(code, timeframe):
    """返回 (fetched_date, fetched_at, klines_list) 或 (None, None, None)。

    fetched_at 是精确时间戳（ISO 格式），用于识别盘中 partial K 线。
    fetched_at 可能为 None（老缓存数据没这字段）。
    """
    _ensure_init()
    conn = sqlite3.connect(_CACHE_DB_PATH)
    try:
        row = conn.execute(
            'SELECT fetched_date, fetched_at, klines_json FROM kline_cache '
            'WHERE code=? AND timeframe=?',
            (code, timeframe),
        ).fetchone()
        if not row:
            return None, None, None
        try:
            return row[0], row[1], json.loads(row[2])
        except Exception as e:
            logger.warning(f'kline_cache JSON 解析失败 {code}: {e}')
            return None, None, None
    finally:
        conn.close()


def store(code, timeframe, klines):
    """写入缓存（INSERT OR REPLACE，自动覆盖旧的 fetched_date / fetched_at）。"""
    _ensure_init()
    now = datetime.datetime.now()
    today = now.date().isoformat()
    now_iso = now.isoformat(timespec='seconds')
    conn = sqlite3.connect(_CACHE_DB_PATH)
    try:
        conn.execute(
            'INSERT OR REPLACE INTO kline_cache '
            '(code, timeframe, fetched_date, fetched_at, klines_json) '
            'VALUES (?, ?, ?, ?, ?)',
            (code, timeframe, today, now_iso, json.dumps(klines, ensure_ascii=False)),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f'kline_cache 写入失败 {code}: {e}')
    finally:
        conn.close()


# 收盘时间（A 股下午 15:00）
_MARKET_CLOSE_TIME = datetime.time(15, 0)


def latest_completed_trading_day(now=None):
    """
    返回最近一个收盘完成的交易日（YYYY-MM-DD 字符串）。

    判断规则：
      - 工作日 ≥ 15:00 → 今天本身
      - 否则（盘前 / 盘中 / 周末）→ 向前找上一个工作日
      - 不考虑节假日（缺数据源；节假日盘前盘后会误判极少数边角，可接受）

    用于 target_date：盘中 / 周末时 target = 昨天，避免把"今日 K 还没收盘"
    误判为"全市场都缺数据"。
    """
    if now is None:
        now = datetime.datetime.now()
    today = now.date()
    if today.weekday() < 5 and now.time() >= _MARKET_CLOSE_TIME:
        return today.isoformat()
    d = today
    while True:
        d -= datetime.timedelta(days=1)
        if d.weekday() < 5:
            return d.isoformat()


def is_today_kline_complete(fetched_at_iso, kline_last_date):
    """
    判断缓存里今日 K 是否完整（收盘后下载）。

    Returns:
        True   完整（收盘后下载，可信）
        False  partial（盘中下载的不完整 K，需要补全）
        None   不适用（K 线 last 不是今天）
    """
    today_str = datetime.date.today().isoformat()
    if not kline_last_date or kline_last_date < today_str:
        return None   # K 线 last 不是今天，不存在 partial 问题
    # K 线 last == today（不应 > today，但兼容处理）
    if not fetched_at_iso:
        return False  # 老数据没 fetched_at → 保守视为 partial
    try:
        fetched_dt = datetime.datetime.fromisoformat(fetched_at_iso)
    except (TypeError, ValueError):
        return False
    today_close_dt = datetime.datetime.combine(datetime.date.today(), _MARKET_CLOSE_TIME)
    return fetched_dt >= today_close_dt


def stats():
    """缓存统计：总行数 / 当日命中行数 / 文件大小。"""
    _ensure_init()
    today = datetime.date.today().isoformat()
    conn = sqlite3.connect(_CACHE_DB_PATH)
    try:
        total      = conn.execute('SELECT COUNT(*) FROM kline_cache').fetchone()[0]
        today_rows = conn.execute(
            'SELECT COUNT(*) FROM kline_cache WHERE fetched_date = ?', (today,),
        ).fetchone()[0]
    finally:
        conn.close()
    size_bytes = os.path.getsize(_CACHE_DB_PATH) if os.path.exists(_CACHE_DB_PATH) else 0
    return {
        'total_rows':   total,
        'today_rows':   today_rows,
        'stale_rows':   total - today_rows,
        'size_mb':      round(size_bytes / 1024 / 1024, 1),
        'db_path':      _CACHE_DB_PATH,
        'today':        today,
    }


def clear():
    """清空缓存表 + VACUUM 立即回收磁盘空间。"""
    _ensure_init()
    conn = sqlite3.connect(_CACHE_DB_PATH)
    try:
        conn.execute('DELETE FROM kline_cache')
        conn.commit()
        # VACUUM 不能在 transaction 里跑
        conn.isolation_level = None
        conn.execute('VACUUM')
    finally:
        conn.close()


def bulk_check_freshness(codes, timeframe='日K'):
    """
    批量查询每只票 K 线缓存的"最后一根日期"，对比目标日期得出哪些需要下载。

    Args:
        codes: list[str] 待检查的股票代码
        timeframe: '日K' 等

    Returns:
        {
            'target_date': 'YYYY-MM-DD',     # 推断的目标日期（max(last_dates) 或 today）
            'today': 'YYYY-MM-DD',           # 日历今天
            'total': N,
            'cached': [code, ...],            # K线最后一根 >= target_date（已有今日数据）
            'missing': [code, ...],           # K线最后一根 < target_date 或 完全无缓存
            'last_dates': {code: 'YYYY-MM-DD' or None},   # 每只票的状态明细
        }

    target_date 推断逻辑：
      - 拿全市场 K 线最后一根日期的最大值 = "已知最新交易日"
      - 跟 today 比取较小值（避免今天还没收盘时把昨天的全市场都判 stale）
    """
    _ensure_init()
    if not isinstance(codes, list) or not codes:
        return {'target_date': None, 'today': None, 'total': 0,
                'cached': [], 'missing': [], 'last_dates': {}}

    today = datetime.date.today().isoformat()
    last_dates  = {}
    fetched_ats = {}    # Phase 7：精确拉取时间，用于 partial 判断
    bar_counts  = {}    # 新股过滤：K 线条数（< 60 视为新上市）
    BATCH = 500   # SQLite 默认 999 参数上限，留余量

    conn = sqlite3.connect(_CACHE_DB_PATH)
    try:
        for i in range(0, len(codes), BATCH):
            batch = codes[i:i + BATCH]
            placeholders = ','.join('?' * len(batch))
            try:
                rows = conn.execute(
                    f'SELECT code, klines_json, fetched_at FROM kline_cache '
                    f'WHERE code IN ({placeholders}) AND timeframe = ?',
                    list(batch) + [timeframe],
                ).fetchall()
            except Exception as e:
                logger.warning(f'kline_cache 批查失败: {e}')
                continue
            for code, klines_json, fetched_at in rows:
                if fetched_at:
                    fetched_ats[code] = fetched_at
                try:
                    klines = json.loads(klines_json)
                    if isinstance(klines, list):
                        # 即使空数组也记录 bar_count = 0，让 new_stocks 判定能识别"确认无数据"
                        bar_counts[code] = len(klines)
                        if klines:
                            last = klines[-1]
                            # 兼容 time 字段是字符串 / 对象的情况
                            t = last.get('time') if isinstance(last, dict) else None
                            if isinstance(t, str):
                                last_dates[code] = t[:10]
                            elif isinstance(t, dict) and t.get('year'):
                                last_dates[code] = (
                                    f"{t.get('year')}-"
                                    f"{int(t.get('month', 1)):02d}-"
                                    f"{int(t.get('day', 1)):02d}"
                                )
                            else:
                                last_dates[code] = None
                except Exception:
                    last_dates[code] = None
    finally:
        conn.close()

    # target_date 用"最近完成收盘的交易日"：盘中 / 周末 → 昨天；工作日 ≥15:00 → today
    # 避免盘中把"今日 K 还没收盘"误判为"全市场都缺数据"导致重复下载 partial 数据
    target_date = latest_completed_trading_day()

    # 是否已收盘 — 决定 partial today 是否算"问题"：
    #   - 盘中 partial 是常态（数据本来还没完成），不应列为待补全
    #   - 收盘后 partial 才算异常（应该有完整 K 但仍是盘中数据），需要补全
    now = datetime.datetime.now()
    today_close_dt = datetime.datetime.combine(datetime.date.today(), _MARKET_CLOSE_TIME)
    is_post_close = now >= today_close_dt and now.weekday() < 5

    # 四类分组：
    #   new_stocks - 缓存里 K 线 < 60 根（≈3 个月），视为新上市，从下载/扫描队列排除
    #   cached  - K 线 last >= target_date 且不是收盘后还停留的 partial today
    #   missing - K 线 last < target_date 或完全无缓存（注意：完全无缓存的新股第一次仍会进 missing 拉一次）
    #   partial - K 线 last == today + fetched_at < 15:00 + 当前已收盘（盘中残留需补）
    cached, missing, partial, new_stocks = [], [], [], []
    NEW_STOCK_BAR_THRESHOLD = 60
    for code in codes:
        # 缓存里有记录 + 条数 < 60 → 视为新股 / pre-IPO（永久跳过到下个交易日重试）
        # 含两种情况：bc == 0（TDX 确认无数据，pre-IPO）/ 0 < bc < 60（上市不久新股）
        if code in bar_counts and bar_counts[code] < NEW_STOCK_BAR_THRESHOLD:
            new_stocks.append(code)
            continue

        last = last_dates.get(code)
        if not last or last < target_date:
            missing.append(code)
            continue
        # 仅在已收盘后才检查 partial today，盘中 partial 算 cached（重下也是盘中数据）
        if last == today and is_post_close:
            complete = is_today_kline_complete(fetched_ats.get(code), last)
            if complete is False:
                partial.append(code)
                continue
        cached.append(code)

    return {
        'target_date': target_date,
        'today':       today,
        'total':       len(codes),
        'cached':      cached,
        'missing':     missing,
        'partial':     partial,         # Phase 7 新增：盘中下载的不完整今日 K
        'new_stocks':  new_stocks,      # 新增：上市 < 60 个交易日的新股
        'last_dates':  last_dates,
    }

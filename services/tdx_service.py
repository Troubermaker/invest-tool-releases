"""
通达信（TDX）行情服务 — 给管理员全量 K 线 + 扫描器用。

为什么要用 pytdx：
  EM/腾讯都是 HTTP/JSON 接口，反爬严格度从中到高，批量请求容易触发风控。
  通达信走的是它客户端跟行情服务器的私有 TCP 协议（pytdx 反向解析了协议），
  完全没有反爬概念 — 因为协议本身就是给客户端用的，怎么打都不会被 ban。

支持的 timeframe：
  - 分时（今日逐分钟）→ get_minute_time_data
  - 5 日（5 日 5 分钟 K，~240 根）→ get_security_bars(category=0)
  - 日 K / 周 K / 月 K / 年 K → get_security_bars(category=9/5/6/11)

设计要点：
  - IP 池 + 自动选最快的服务器（首次连接时测速，后续复用）
  - 全局单例 connection（线程安全）
  - 失败时自动换 IP 重连（TDX 服务器偶尔会重启）
  - 输出格式与 services/kline_service 完全对齐，调用方无需感知数据源差异

⚠ 仅 admin 调用：单独的 API 端点 + admin_only 装饰器双重门禁。
"""
import datetime
import logging
import sys
import threading
import time

logger = logging.getLogger(__name__)
# 项目没全局 logging.basicConfig，给 tdx_service 单独配 INFO 输出
# 不影响其它模块（其它模块继续走默认 WARNING）
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _fmt = logging.Formatter('[%(asctime)s] [TDX] %(message)s', datefmt='%H:%M:%S')

    # stdout handler（开发模式 python main.py 看终端）
    _h_out = logging.StreamHandler(sys.stdout)
    _h_out.setFormatter(_fmt)
    logger.addHandler(_h_out)

    # 文件 handler（打包后/无终端启动也能查日志）
    # 路径：项目根 / logs / tdx.log，按文件大小滚动（5MB × 3）
    try:
        import os as _os
        from logging.handlers import RotatingFileHandler as _RFH
        _log_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'logs')
        _os.makedirs(_log_dir, exist_ok=True)
        _h_file = _RFH(
            _os.path.join(_log_dir, 'tdx.log'),
            maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8',
        )
        _h_file.setFormatter(_fmt)
        logger.addHandler(_h_file)
    except Exception as _e:
        # 文件 handler 失败不影响 stdout
        sys.stdout.write(f'[TDX] 文件日志初始化失败: {_e}\n')

    logger.propagate = False  # 避免重复输出到 root logger

# 通达信主流行情服务器 IP 池（每月偶尔会变，但下面这批多年稳定）
# 优先选 BGP 多线和电信线路，避免跨网延迟
TDX_HOSTS = [
    ('119.147.212.81',  7709),  # 深圳-电信
    ('113.105.142.162', 7709),  # 深圳-电信
    ('120.79.60.82',    7709),  # 深圳-阿里云
    ('218.6.170.47',    7709),  # 成都-电信
    ('124.71.85.110',   7709),  # 上海-华为云
    ('123.125.108.14',  7709),  # 北京-联通
    ('114.80.149.19',   7709),  # 上海-电信
    ('218.108.50.178',  7709),  # 杭州-电信
    ('60.12.136.250',   7709),  # 杭州-电信
    ('115.238.90.165',  7709),  # 杭州-电信
]

# pytdx 类目对应 timeframe（与 services/kline_service.KLT_MAP 对齐）
# pytdx 的 category 编码：0=5min, 1=15min, 2=30min, 3=60min, 4=日, 5=周, 6=月,
#                        7=1min(扩展), 8=1min, 9=RI_K(部分服务器返空), 10=季, 11=年
# 日 K 用 category=4（KLINE_TYPE_DAILY，最通用）；某些服务器对 category=9 返空
_TDX_CATEGORY = {
    '日K': 4,
    '周K': 5,
    '月K': 6,
    '年K': 11,
}

# 全局连接 + 锁
_api_lock = threading.Lock()
_api = None
_active_host = None       # (ip, port)
_last_health_check = 0.0
_HEALTH_CHECK_INTERVAL = 60.0   # 60s 内不重测速


def _detect_market(code):
    """
    A 股代码判市场：
      6xxx → 沪市（market=1）
      0xx / 3xx → 深市（market=0）
      4xxx / 8xxx / 920xxx → 北交所（pytdx 不支持，返 -1 让上层走 fallback）
    """
    code = (code or '').strip()
    if not code:
        return -1
    if code.startswith('6'):
        return 1
    if code.startswith(('0', '3')):
        return 0
    return -1   # 北交所不支持


def _connect_best_host(exclude=None):
    """从 IP 池里挑一个能连的，并发探测，先有 3 个能用的就够选最快。
    exclude: set of (ip, port) 跳过这些 host"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pytdx.hq import TdxHq_API
    exclude = exclude or set()

    targets = [(ip, port) for ip, port in TDX_HOSTS if (ip, port) not in exclude]
    if not targets:
        return None, None

    def _probe(ip, port):
        """单个 IP 探测：connect + 一个轻量请求验证可用性。返回成功的 api 或 None。"""
        api = TdxHq_API()
        t0 = time.monotonic()
        try:
            if not api.connect(ip, port, time_out=1.5):
                try: api.disconnect()
                except Exception: pass
                return None
            latency = time.monotonic() - t0
            bars = api.get_security_bars(4, 0, '000001', 0, 1)
            if bars:
                return (latency, ip, port, api)
            try: api.disconnect()
            except Exception: pass
        except Exception:
            try: api.disconnect()
            except Exception: pass
        return None

    candidates = []
    # 一池子全并发探，固定 2s 总超时
    with ThreadPoolExecutor(max_workers=len(targets)) as ex:
        futures = {ex.submit(_probe, ip, port): (ip, port) for ip, port in targets}
        for fut in as_completed(futures, timeout=3.0):
            try:
                result = fut.result(timeout=0.1)
            except Exception:
                continue
            if result:
                candidates.append(result)
                if len(candidates) >= 3:
                    break  # 找到 3 个能用的就够选了

    # 关掉超时未完成的（会在后台继续跑直到 connect 自己超时；不影响主流程）
    if not candidates:
        logger.error(f'TDX 所有服务器连接失败 (excluded={exclude or "none"})')
        return None, None

    candidates.sort(key=lambda x: x[0])
    best_lat, best_ip, best_port, best_api = candidates[0]
    for _, _, _, api in candidates[1:]:
        try: api.disconnect()
        except Exception: pass
    logger.warning(f'TDX 连接已建立 → {best_ip}:{best_port} ({best_lat * 1000:.0f}ms)')
    return best_api, (best_ip, best_port)


def _ensure_api():
    """获取（或重建）全局 TDX API 连接。线程安全。"""
    global _api, _active_host, _last_health_check
    with _api_lock:
        now = time.monotonic()
        if _api is not None and (now - _last_health_check < _HEALTH_CHECK_INTERVAL):
            return _api
        # 健康检查：如果连接坏了重建
        if _api is not None:
            try:
                bars = _api.get_security_bars(4, 0, '000001', 0, 1)
                if bars:
                    _last_health_check = now
                    return _api
            except Exception:
                logger.warning('TDX 现有连接失效，重连')
            try: _api.disconnect()
            except Exception: pass
            _api = None
        # 重建
        api, host = _connect_best_host()
        if api is None:
            return None
        _api = api
        _active_host = host
        _last_health_check = now
        return _api


def _force_reconnect():
    """强制断开当前连接、换一个 host 重连（排除当前刚失败的 host）。"""
    global _api, _active_host, _last_health_check
    with _api_lock:
        bad_host = _active_host
        if _api is not None:
            try: _api.disconnect()
            except Exception: pass
            _api = None
        # 排除刚刚返空的 host，强制选别的
        exclude = {bad_host} if bad_host else set()
        api, host = _connect_best_host(exclude=exclude)
        if api is None:
            # 排除后没的选了，整池子换一遍（说明刚才那个不算"坏"，可能是单股票数据问题）
            api, host = _connect_best_host()
            if api is None:
                return None
        _api = api
        _active_host = host
        _last_health_check = time.monotonic()
        return _api


def _to_kline_dict(row, prev_close):
    """把 pytdx 单根 bar 转成跟 services/kline_service 对齐的 dict。"""
    op = float(row['open'])
    cl = float(row['close'])
    hi = float(row['high'])
    lo = float(row['low'])
    vol = float(row.get('vol', 0)) / 10000          # 手 → 万手
    amt = float(row.get('amount', 0)) / 100000000   # 元 → 亿
    chg = cl - prev_close if prev_close is not None else 0
    pct = (chg / prev_close * 100) if prev_close else 0
    amp = ((hi - lo) / prev_close * 100) if prev_close else 0
    # pytdx 提供 year/month/day（部分版本是 datetime str），统一成 'YYYY-MM-DD'
    if 'datetime' in row:
        time_str = str(row['datetime']).split(' ')[0]
    else:
        time_str = f"{row['year']:04d}-{row['month']:02d}-{row['day']:02d}"
    return {
        'time': time_str,
        'open': op, 'close': cl, 'high': hi, 'low': lo,
        'vol': vol, 'amt': amt,
        'chg': chg, 'pct': pct, 'amp': amp,
    }


def _get_prev_close(api, market, code):
    """获取前一交易日收盘价（分时/5日 计算 chg/pct 用）。"""
    try:
        bars = api.get_security_bars(4, market, code, 0, 2)
        if bars and len(bars) >= 2:
            return float(bars[-2]['close'])
        if bars:
            return float(bars[-1]['close'])
    except Exception:
        pass
    return None


def _fetch_minute_today(api, market, code):
    """
    分时（最近交易日逐分钟）— 用 1 分钟 K（category=8）实现，比 get_minute_time_data 可靠：
      - 每根 K 自带 year/month/day/hour/minute，时间无歧义
      - 午休天然跳过（pytdx 的 1min K 不会在 11:30-13:00 输出空根）
      - vol/amount 单位与日 K 一致
    周末/节假日时取最近一个交易日的分时（与 EM trends2 行为一致）。
    返回 kline_service._fetch_minute 对齐的 list[dict]：{time, value, vol, amt, chg, pct}
    """
    with _api_lock:
        raw = api.get_security_bars(8, market, code, 0, 240)
    if not raw:
        return []

    # 取响应里最后一根的日期，过滤同日的所有根
    # — 这样能正确处理周末/节假日（取最近交易日数据，跟 EM trends2 行为一致）
    last = raw[-1]
    ty, tm, td = last.get('year'), last.get('month'), last.get('day')
    today_bars = [r for r in raw
                  if r.get('year') == ty
                  and r.get('month') == tm
                  and r.get('day') == td]
    if not today_bars:
        return []

    prev_close = _get_prev_close(api, market, code)
    tz = datetime.timezone(datetime.timedelta(hours=8))

    results = []
    for r in today_bars:
        cl = float(r.get('close') or 0)
        if cl <= 0:
            continue
        try:
            dt = datetime.datetime(
                r['year'], r['month'], r['day'],
                r.get('hour', 9), r.get('minute', 30),
                tzinfo=tz,
            )
        except Exception:
            continue
        vol = float(r.get('vol') or 0) / 10000           # 手 → 万手
        amt = float(r.get('amount') or 0) / 100000000    # 元 → 亿
        chg = (cl - prev_close) if prev_close else 0
        pct = (chg / prev_close * 100) if prev_close else 0
        results.append({
            'time': int(dt.timestamp()),
            'value': cl,
            'vol': vol,
            'amt': amt if amt > 0 else None,
            'chg': chg,
            'pct': pct,
        })
    return results


def _fetch_5day(api, market, code):
    """
    5 日（5 日 5 分钟 K）。
    pytdx category=0 是 5min K，单次最多 800 根；240 根正好覆盖近 5 个交易日。
    返回与 kline_service._fetch_5day 对齐的 list[dict]。
    """
    with _api_lock:
        raw = api.get_security_bars(0, market, code, 0, 240)
    if not raw:
        return []
    prev_close = _get_prev_close(api, market, code)

    results = []
    rolling_prev = prev_close
    for row in raw:
        op = float(row['open'])
        cl = float(row['close'])
        hi = float(row['high'])
        lo = float(row['low'])
        vol = float(row.get('vol', 0)) / 10000
        amt = float(row.get('amount', 0)) / 100000000
        chg = cl - rolling_prev if rolling_prev else 0
        pct = (chg / rolling_prev * 100) if rolling_prev else 0
        amp = ((hi - lo) / rolling_prev * 100) if rolling_prev else 0
        # pytdx 5min K 时间字段：year/month/day + hour/minute
        try:
            dt = datetime.datetime(
                row['year'], row['month'], row['day'],
                row.get('hour', 9), row.get('minute', 30),
                tzinfo=datetime.timezone(datetime.timedelta(hours=8))
            )
            ts = int(dt.timestamp())
        except Exception:
            ts = 0
        results.append({
            'time': ts,
            'open': op, 'high': hi, 'low': lo, 'close': cl,
            'value': cl,
            'vol': vol,
            'chg': chg, 'pct': pct, 'amp': amp,
            'amt': amt,
        })
        rolling_prev = cl
    return results


PYTDX_MAX_PER_REQUEST = 800   # pytdx 协议单次请求上限


def _fetch_paged(api, category, market, code, total):
    """
    分页拉取 total 根 K 线。pytdx 单次最多 800 根，>800 时分多次拉。
    pytdx 分页语义：start=0 是最新一根，start=N 跳过最新 N 根。
    每个 chunk 内部按时间正序（旧→新），多 chunk 拼回完整时间序列。

    每次 pytdx 调用后在锁内额外 sleep 50ms — 防止扫描器密集批量调用时
    响应缓冲串包到下一次（即所谓 mini sparkline 翻车的同一类问题）。
    """
    if total <= PYTDX_MAX_PER_REQUEST:
        with _api_lock:
            result = api.get_security_bars(category, market, code, 0, total)
            time.sleep(0.05)
        return result

    # 分页：从最新往旧拉
    chunks = []
    offset = 0
    while offset < total:
        n = min(PYTDX_MAX_PER_REQUEST, total - offset)
        with _api_lock:
            chunk = api.get_security_bars(category, market, code, offset, n)
            time.sleep(0.05)
        if not chunk:
            break
        chunks.append(chunk)         # chunks[0] 是最新段，chunks[-1] 是最旧段
        if len(chunk) < n:
            break                    # 已到上市日，没有更老数据了
        offset += n

    # 拼接：chunks[-1] + ... + chunks[0]（按时间正序）
    result = []
    for chunk in reversed(chunks):
        result.extend(chunk)
    return result


def get_stock_kline(code, timeframe='日K', count=800):
    """
    拉个股 K 线（通达信源）。
    返回与 services.kline_service.get_stock_kline 完全相同的 list[dict]。
    失败返回 [] 让上层 fallback 到腾讯/EM。
    支持的 timeframe：分时 / 5日 / 日K / 周K / 月K / 年K
    count > 800 自动分页（每页 800），最多可拉至股票上市日全历史。
    """
    market = _detect_market(code)
    if market < 0:
        logger.info(f'TDX 跳过 {code}（北交所，pytdx 不支持）')
        return []

    api = _ensure_api()
    if api is None:
        logger.warning(f'TDX 连接不可用，{code} 走 fallback')
        return []

    try:
        # 分时 / 5 日 走专用接口，其余走 K 线接口
        if timeframe == '分时':
            # 某些股票（小市值 / 偏门票）单 host 偶尔会返空，给一次换 host 重试机会，
            # 跟下面 _fetch_paged 的 retry 思路一致
            data = _fetch_minute_today(api, market, code)
            if data:
                return data
            logger.warning(f'TDX {code} 分时 当前服务器返空，换 host 重试')
            api = _force_reconnect()
            if api is None:
                return []
            data = _fetch_minute_today(api, market, code)
            if not data:
                logger.warning(f'TDX {code} 分时 重试后仍为空，返回 []')
            return data
        if timeframe == '5日':
            return _fetch_5day(api, market, code)

        category = _TDX_CATEGORY.get(timeframe)
        if category is None:
            return []   # 未知 timeframe，让 caller fallback

        # 空响应 = 当前服务器对这只票数据覆盖不全，换一个 host 再试一次
        raw = None
        for attempt in range(2):
            raw = _fetch_paged(api, category, market, code, count)
            if raw:
                break
            if attempt == 0:
                logger.warning(f'TDX {code} {timeframe} 当前服务器返空，换 host 重试')
                api = _force_reconnect()
                if api is None:
                    break

        if not raw:
            logger.warning(f'TDX 两个服务器都返空：{code} {timeframe} (market={market})')
            return []

        results = []
        prev_close = None
        for row in raw:
            d = _to_kline_dict(row, prev_close)
            results.append(d)
            prev_close = d['close']
        logger.info(f'{code} {timeframe} 拿到 {len(results)} 根')
        return results
    except Exception as e:
        logger.warning(f'TDX get_stock_kline({code}, {timeframe}) 异常：{e}')
        # 标记连接需要重建
        global _last_health_check
        _last_health_check = 0
        return []


# =========== Phase 7：TDX 式增量更新辅助 ===========

def _normalize_kline_time_key(t):
    """K 线 time 字段 → 'YYYY-MM-DD' 字符串（dict key 用）。无法识别返 None。"""
    if isinstance(t, str):
        return t[:10] if len(t) >= 10 else None
    if isinstance(t, dict) and t.get('year'):
        try:
            return f"{int(t['year'])}-{int(t.get('month', 1)):02d}-{int(t.get('day', 1)):02d}"
        except (TypeError, ValueError):
            return None
    if isinstance(t, (int, float)):
        try:
            ts = t / 1000 if t > 1e12 else t
            return datetime.date.fromtimestamp(ts).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    return None


def _extract_last_kline_date(klines):
    """K 线列表最后一根的日期字符串。"""
    if not klines:
        return None
    last = klines[-1] if isinstance(klines[-1], dict) else None
    return _normalize_kline_time_key(last.get('time')) if last else None


def _calendar_days_between(d1_str, d2_str):
    """d2 - d1 的日历天数。d1 ≥ d2 返 0；解析异常返 -1。"""
    try:
        d1 = datetime.date.fromisoformat(d1_str[:10])
        d2 = datetime.date.fromisoformat(d2_str[:10])
        return max(0, (d2 - d1).days)
    except (TypeError, ValueError, AttributeError):
        return -1


def _merge_klines_by_time(old, new):
    """按 time 去重合并；new 覆盖 old 同日记录。返回时间升序列表。"""
    by_time = {}
    for k in old or []:
        if isinstance(k, dict):
            tk = _normalize_kline_time_key(k.get('time'))
            if tk:
                by_time[tk] = k
    for k in new or []:
        if isinstance(k, dict):
            tk = _normalize_kline_time_key(k.get('time'))
            if tk:
                by_time[tk] = k   # 覆盖（处理盘中/盘后差异、复权微调）
    return [by_time[t] for t in sorted(by_time.keys())]


def _detect_adjustment(old_klines, new_klines, threshold=0.01):
    """检测复权除息：重叠日期的 close 价平均差异 > threshold（默认 1%）→ 视为复权。"""
    if not old_klines or not new_klines:
        return False
    old_by_time = {}
    for k in old_klines:
        if isinstance(k, dict):
            tk = _normalize_kline_time_key(k.get('time'))
            if tk:
                old_by_time[tk] = k

    diffs = []
    for nk in new_klines:
        if not isinstance(nk, dict):
            continue
        tk = _normalize_kline_time_key(nk.get('time'))
        if not tk or tk not in old_by_time:
            continue
        try:
            old_close = float(old_by_time[tk].get('close') or 0)
            new_close = float(nk.get('close') or 0)
            if old_close > 0 and new_close > 0:
                diffs.append(abs(new_close - old_close) / old_close)
        except (TypeError, ValueError):
            continue

    if len(diffs) < 2:        # 重叠样本太少，不下结论
        return False
    return (sum(diffs) / len(diffs)) > threshold


def _fetch_and_cache_full(code, timeframe, count):
    """全量拉 + 写缓存（替换语义）。

    Phase 7：即使 result == []（TDX 多次确认返空，pre-IPO / 退市 / 暂停交易等）
    也写缓存——同日后续调用直接命中空缓存，避免反复 ping TDX。
    跨日后会自动重试一次（看是否正式上市），还是空就再缓存。

    例外：TDX 完全连接不上时不写缓存（避免把网络问题误标为"无数据"）。
    例外 2：已有非空缓存时，TDX 返空不要覆盖（多半是网络瞬断/服务器抖动，
    保留老 K 比清空安全；下次再拉就行）。
    """
    import services.kline_cache_service as kline_cache

    # TDX 不可用就不写 cache（network glitch 不该污染缓存）
    if _ensure_api() is None:
        return []

    result = get_stock_kline(code, timeframe, count)

    # 防御性检查：TDX 返空 + 已有非空缓存 → 保留老缓存（不写 [] 把历史 K 删掉）
    # 仅"完全没缓存"或"老缓存本身是空"时才允许写空（pre-IPO 语义保留）
    if not result:
        _, _, existing = kline_cache.lookup_full(code, timeframe)
        if existing:   # 老缓存有 bars → 不动它
            logger.warning(f'TDX {code} 全量返空但已有 {len(existing)} 根缓存，保留不覆盖')
            return existing
        # 没缓存或缓存就是空 → 写 [] 表示"已确认无数据"
        kline_cache.store(code, timeframe, [])
        return []

    kline_cache.store(code, timeframe, result)
    return result


def get_stock_kline_cached(code, timeframe='日K', count=800):
    """
    带 SQLite 持久化缓存的 K 线获取（admin only 端点暴露给前端回测/扫描器用）。

    Phase 7 升级：TDX 式增量更新
      - 同日缓存命中 → 直接返回（最快）
      - 跨日 + 缓存有 → 算 gap，只拉 gap+5 根新 K，merge 后写回（增量）
      - gap > 30 天 / 检测到复权调整 / 完全无缓存 → 全量重拉 800 根
      - 非日 K（周/月/年）→ 全量（数据量本就小，不必增量）

    传输量从 80KB / 只降到几百字节 / 只，跨日全市场更新 25 分钟 → 5 分钟。
    """
    import services.kline_cache_service as kline_cache

    market = _detect_market(code)
    if market < 0:
        return []

    import services.kline_cache_service as _kcache
    today = datetime.date.today().isoformat()
    cached_date, cached_fetched_at, cached_klines = kline_cache.lookup_full(code, timeframe)

    # 周末 / 节假日「最近完成交易日」：用于非日 K 的缓存命中比较
    # 周日跑数据时 today=Sun，但 周K/月K 在 Friday 15:00 后就定型了，没必要重拉
    last_trading_day = _kcache.latest_completed_trading_day()

    # 非日 K（周/月/年）特殊路径：cache 命中条件比 日K 宽松
    #   - 数据周期长，单根 K 在周内/月内/年内会持续更新，但跨周期才"定型"
    #   - 周末跑：cached_date >= last_trading_day（即 Friday 拉的）→ 直接命中，不再调 TDX
    #   - 工作日跑：cached_date == today → 同日已拉过，直接命中
    if timeframe != '日K':
        if cached_klines and cached_date >= last_trading_day:
            return cached_klines
        # 缓存陈旧 / 完全无缓存 → 全量重拉
        return _fetch_and_cache_full(code, timeframe, count)

    # 以下是日 K 路径

    # 周末 / 节假日命中：今天非交易日，缓存的 last K 已经是最近交易日 → 不需重拉
    # 例：周六 cached last_date='2026-05-08'(Fri) >= last_trading_day='2026-05-08' → 命中
    # 工作日命中也包含在里面：盘前 last_trading_day=昨天，缓存有昨天 K 即可
    # 但盘后 last_trading_day=今天，没今天 K 不命中，会落入下面的跨日逻辑（正常）
    if cached_klines:
        cached_last_date = _extract_last_kline_date(cached_klines)
        if cached_last_date and cached_last_date >= last_trading_day:
            # 但要排除"last K 是今天 + fetched_at 在收盘前 + 现在已收盘"的 partial 情况
            # 这种情况要走下面的同日 partial 补全逻辑
            now = datetime.datetime.now()
            today_close_dt = datetime.datetime.combine(
                datetime.date.today(), datetime.time(15, 0),
            )
            is_post_close = now >= today_close_dt and now.weekday() < 5
            is_partial_today = (
                cached_last_date == today
                and is_post_close
                and kline_cache.is_today_kline_complete(cached_fetched_at, cached_last_date) is False
            )
            if not is_partial_today:
                return cached_klines

    # 同日 + 确认空缓存（TDX 多次返空，多半 pre-IPO）→ 直接返 [] 不再调 TDX
    # 跨日时会重试一次看是否上市（cached_date != today，会走全量分支）
    if cached_date == today and cached_klines == []:
        return []

    # 同日缓存 → 最快路径，但要检测两种"陈旧"情况：
    #   A) last_date == today 但 fetched_at < 15:00 → partial today K（盘中拉的）
    #   B) last_date < today                        → 之前拉时 TDX 没给今日 K（最常见）
    # 已收盘后两种都应该重拉。否则同日内缓存命中直接返回。
    if cached_date == today and cached_klines:
        if timeframe == '日K':
            last_date = _extract_last_kline_date(cached_klines)
            now = datetime.datetime.now()
            today_close_dt = datetime.datetime.combine(
                datetime.date.today(), datetime.time(15, 0),
            )
            is_post_close = now >= today_close_dt and now.weekday() < 5

            # 情况 B：last_date < today 且已收盘 → 缓存已陈旧，跳出同日分支重拉
            if is_post_close and last_date and last_date < today:
                logger.info(f'TDX {code} 同日缓存但 last={last_date}<today，已收盘 → 重拉补今日 K')
                # 走下面的"跨日增量"分支：会算 gap、拉新 K、merge 写回
                pass  # 不 return，下落
            else:
                # 情况 A：last_date == today + partial（fetched_at < 15:00）
                complete = kline_cache.is_today_kline_complete(cached_fetched_at, last_date)
                if complete is False and is_post_close:
                    logger.info(f'TDX {code} 检测到盘中 partial today K，已收盘 → 自动补全')
                    new_klines = get_stock_kline(code, timeframe, 5)
                    if new_klines and not _detect_adjustment(cached_klines, new_klines):
                        merged = _merge_klines_by_time(cached_klines, new_klines)
                        kline_cache.store(code, timeframe, merged)
                        return merged
                    if new_klines:   # 复权命中 → 全量
                        return _fetch_and_cache_full(code, timeframe, count)
                    # 拉失败 → 继续返 partial 数据（fall through return cached）
                return cached_klines
        # else: 非日 K 已在函数顶部 timeframe != '日K' 分支处理，到不了这里

    # 完全无缓存 → 全量首拉
    if not cached_klines:
        return _fetch_and_cache_full(code, timeframe, count)

    # 跨日：判断 gap 决定增量 vs 全量
    last_date = _extract_last_kline_date(cached_klines)
    if not last_date:
        return _fetch_and_cache_full(code, timeframe, count)

    gap = _calendar_days_between(last_date, today)

    if gap == 0:
        # K 线最后一根已经是今天（可能是凌晨切日，fetched_date 是昨天但数据其实有今日 K）
        # 重写一下 fetched_date = today，下次直接命中
        kline_cache.store(code, timeframe, cached_klines)
        return cached_klines

    if gap < 0 or gap > 30:
        # gap 解析失败 / 缓存太老（>30天）→ 全量重拉
        return _fetch_and_cache_full(code, timeframe, count)

    # gap 1-30 天 → 增量拉 (gap + 5) 根，buffer 用于复权检测 + 节假日跳跃容错
    fetch_count = min(gap + 5, 50)
    new_klines = get_stock_kline(code, timeframe, fetch_count)
    if not new_klines:
        # 增量失败 → 退回全量重拉（TDX 对小 count 偶尔返空，但同一只票全量往往能拿到）
        # 全量本身已写缓存；若它也失败会返 []，让前端识别为真失败
        logger.info(f'TDX {code} 增量返空，退回全量重拉')
        return _fetch_and_cache_full(code, timeframe, count)

    # 复权检测：重叠日期的 close 平均偏差 > 1% → 全量重拉
    if _detect_adjustment(cached_klines, new_klines):
        logger.warning(f'TDX {code} 检测到复权调整，全量重拉')
        return _fetch_and_cache_full(code, timeframe, count)

    merged = _merge_klines_by_time(cached_klines, new_klines)
    kline_cache.store(code, timeframe, merged)
    return merged


def is_available():
    """探测当前 TDX 是否可用（admin 解锁 UI 上可显示连接状态）。"""
    return _ensure_api() is not None


# A 股代码段：6xxx (沪) / 000-003xxx, 300xxx (深)；排除北交所 4xxx/8xxx/920xxx 和 ETF/基金
_A_SHARE_PREFIXES_SH = ('600', '601', '603', '605', '688')   # 沪市主板 + 科创板
_A_SHARE_PREFIXES_SZ = ('000', '001', '002', '003', '300')   # 深市主板 + 中小板 + 创业板


def _is_a_share_code(code):
    code = (code or '').strip()
    if len(code) != 6 or not code.isdigit():
        return False
    return code.startswith(_A_SHARE_PREFIXES_SH) or code.startswith(_A_SHARE_PREFIXES_SZ)


# A 股代码列表缓存：内存 + 磁盘双层
# 内存：(cached_date, codes) — 同进程秒级返回
# 磁盘：JSON 文件（cache 目录下）— 跨重启留存，按交易日命中
_a_share_codes_cache = (None, [])     # (cached_date_str, codes)
_a_share_codes_lock = threading.Lock()


def _a_share_codes_disk_path():
    """磁盘缓存路径：跟 kline_cache.db 同目录。"""
    import os
    import services.kline_cache_service as _kc
    return os.path.join(os.path.dirname(_kc._CACHE_DB_PATH), 'a_share_codes.json')


def _load_a_share_codes_disk():
    """读磁盘缓存。返回 (cached_date_str, codes) 或 (None, [])。"""
    import os
    import json
    path = _a_share_codes_disk_path()
    if not os.path.exists(path):
        return None, []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get('codes'), list):
            return data.get('cached_date'), data['codes']
    except Exception as e:
        logger.warning(f'A 股代码磁盘缓存读取失败：{e}')
    return None, []


def _save_a_share_codes_disk(codes):
    """写磁盘缓存（cached_date = 今天）。"""
    import json
    try:
        path = _a_share_codes_disk_path()
        today = datetime.date.today().isoformat()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'cached_date': today, 'codes': codes}, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f'A 股代码磁盘缓存写入失败：{e}')


def _enumerate_a_share_codes_once():
    """单次枚举尝试。返回 [] 表示失败 / 空。"""
    api = _ensure_api()
    if api is None:
        logger.warning('TDX 不可用，无法枚举 A 股列表')
        return []

    results = []
    PAGE = 1000  # pytdx get_security_list 单次最多 1000 条
    for market_id in (0, 1):  # 0=深市, 1=沪市
        try:
            count = api.get_security_count(market_id)
        except Exception as e:
            logger.warning(f'TDX get_security_count 失败 market={market_id}: {e}')
            continue
        if not count or count <= 0:
            continue
        for start in range(0, count, PAGE):
            try:
                page = api.get_security_list(market_id, start)
            except Exception as e:
                logger.warning(f'TDX get_security_list 失败 market={market_id} start={start}: {e}')
                continue
            if not page:
                continue
            for item in page:
                code = str(item.get('code', '')).strip()
                if not _is_a_share_code(code):
                    continue
                actual_market = 'SH' if code.startswith(_A_SHARE_PREFIXES_SH) else 'SZ'
                results.append({
                    'code': code,
                    'name': str(item.get('name', '')).strip(),
                    'market': actual_market,
                })

    seen = set()
    deduped = []
    for s in results:
        if s['code'] in seen:
            continue
        seen.add(s['code'])
        deduped.append(s)
    return deduped


def get_all_a_share_codes():
    """
    通过 TDX 枚举沪深两市全部 A 股代码。约 4500-5500 条。

    缓存策略：
      1) 内存命中（同进程同日）→ 秒级返回
      2) 磁盘命中（cached_date >= 最近完成交易日）→ 返回，不调 TDX
         - 周末/节假日：cached_date = 节前最后交易日 → 必命中
         - 工作日盘前：cached_date = 昨日 → 命中（早间没新 IPO）
         - 工作日盘后：cached_date = 今日 → 命中
      3) 缓存陈旧或无 → 调 TDX 枚举（失败换 host 重试一次）
      4) TDX 全失败但磁盘有任意旧缓存 → 兜底返回旧缓存（带警告）

    Returns:
        [{code, name, market}, ...] — market: 'SH' / 'SZ'
        失败返 []（磁盘也没有兜底缓存）
    """
    global _a_share_codes_cache
    import services.kline_cache_service as _kc
    today = datetime.date.today().isoformat()
    last_trading_day = _kc.latest_completed_trading_day()

    # 1) 内存缓存 — 仅同日命中
    with _a_share_codes_lock:
        mem_date, mem_codes = _a_share_codes_cache
        if mem_codes and mem_date == today:
            return mem_codes

    # 2) 磁盘缓存 — cached_date >= last_trading_day 即命中
    disk_date, disk_codes = _load_a_share_codes_disk()
    if disk_codes and disk_date and disk_date >= last_trading_day:
        # 命中：写回内存缓存
        with _a_share_codes_lock:
            _a_share_codes_cache = (disk_date, disk_codes)
        logger.warning(f'TDX A 股枚举命中磁盘缓存（{disk_date}）：{len(disk_codes)} 条，跳过 TDX')
        return disk_codes

    # 3) 调 TDX 枚举（失败重试）
    deduped = _enumerate_a_share_codes_once()
    if not deduped:
        logger.warning('TDX A 股枚举返 0 条，换 host 重试')
        new_api = _force_reconnect()
        if new_api is not None:
            deduped = _enumerate_a_share_codes_once()

    if deduped:
        # 写双层缓存
        with _a_share_codes_lock:
            _a_share_codes_cache = (today, deduped)
        _save_a_share_codes_disk(deduped)
        logger.warning(f'TDX 全市场 A 股枚举完成：{len(deduped)} 条')
        return deduped

    # 4) 兜底：TDX 失败但磁盘还有旧缓存 → 用旧的（带警告，不让用户彻底无法用）
    if disk_codes:
        with _a_share_codes_lock:
            _a_share_codes_cache = (disk_date, disk_codes)
        logger.warning(
            f'TDX 枚举失败，回退到磁盘旧缓存（{disk_date}）：{len(disk_codes)} 条 — '
            '可能漏掉新 IPO/退市，但能继续工作'
        )
        return disk_codes

    logger.warning('TDX 全市场 A 股枚举完成：0 条（TDX + 磁盘缓存都没有）')
    return []


# 指数代码映射：display_name → (market, code)
# market: 1=沪市指数, 0=深市指数
_INDEX_MAP = {
    '上证指数': (1, '000001'),
    '深证成指': (0, '399001'),
    '创业板指': (0, '399006'),
    '科创50':   (1, '000688'),
    '沪深300':  (1, '000300'),
    '中证500':  (1, '000905'),
    '中证1000': (1, '000852'),
}


def get_index_kline(name, timeframe='日K', count=300):
    """
    指数 K 线（pytdx get_index_bars，跟个股是不同协议命令）。
    严格 TDX-only：失败返 []，绝不 fallback 到腾讯/EM。
    """
    target = _INDEX_MAP.get(name)
    if target is None:
        logger.warning(f'TDX 不识别的指数名：{name}')
        return []
    market, code = target

    api = _ensure_api()
    if api is None:
        return []

    category = _TDX_CATEGORY.get(timeframe)
    if category is None:
        return []

    try:
        n = min(count, PYTDX_MAX_PER_REQUEST)
        # pytdx 用 get_index_bars 拉指数，参数同 get_security_bars
        with _api_lock:
            raw = api.get_index_bars(category, market, code, 0, n)
        if not raw:
            logger.warning(f'TDX 指数返空：{name} {timeframe}')
            return []
        results = []
        prev_close = None
        for row in raw:
            d = _to_kline_dict(row, prev_close)
            results.append(d)
            prev_close = d['close']
        logger.info(f'指数 {name} {timeframe} 拿到 {len(results)} 根')
        return results
    except Exception as e:
        logger.warning(f'TDX get_index_kline({name}) 异常：{e}')
        return []


def get_batch_sparklines(codes):
    """
    管理员专用：批量拉今日分时（替代 EM 的 trends2）。
    用 1 分钟 K（category=8）替代 get_minute_time_data，避免后者的时间错位/单位歧义。
    返回 {code: {preClose, prices, avgPrices}}，与 sparkline_service 输出格式一致。
    北交所跳过；今日无数据的票不在返回里。
    """
    if not codes:
        return {}

    api = _ensure_api()
    if api is None:
        return {}

    # 准备 (market, code) 对（剔除北交所）
    market_codes = []
    for code in codes:
        m = _detect_market(code)
        if m >= 0:
            market_codes.append((m, str(code).strip()))
    if not market_codes:
        return {}

    # 1) 批量取 last_close（pytdx get_security_quotes 单次最多 80 只）
    prev_closes = {}
    for i in range(0, len(market_codes), 50):
        batch = market_codes[i:i + 50]
        try:
            with _api_lock:
                quotes = api.get_security_quotes(batch)
            for q in (quotes or []):
                c = q.get('code')
                if c:
                    prev_closes[c] = float(q.get('last_close') or 0)
        except Exception as e:
            logger.warning(f'TDX 批量 quote 失败：{e}')

    # 2) 每只票拉 1 分钟 K
    # ⚠ pytdx 在同一连接上密集顺序调用 get_security_bars 会出现"响应缓冲串包"
    # （第 N 只 stock 实际拿到第 N-1 只的字节）。对策：
    #   a. 每次调用之间留 50ms 间隔，让对端把上次响应的最后一个分包送完
    #   b. 比对前后两只票的完整价格序列，相同 → 串包，强制重连重拉
    def _process_bars(raw, prev_close):
        if not raw:
            return None
        last = raw[-1]
        ty, tm, td = last.get('year'), last.get('month'), last.get('day')
        today_bars = [r for r in raw
                      if r.get('year') == ty
                      and r.get('month') == tm
                      and r.get('day') == td]
        if not today_bars:
            return None
        prices = []
        avg_prices = []
        cum_vol = 0.0
        cum_amt_real = 0.0
        for r in today_bars:
            price = float(r.get('close') or 0)
            if price <= 0:
                continue
            vol = float(r.get('vol') or 0)
            amt_real = float(r.get('amount') or 0)
            cum_vol += vol
            cum_amt_real += amt_real
            prices.append(price)
            avg_prices.append(
                (cum_amt_real / cum_vol / 100) if cum_vol > 0 else price
            )
        if not prices:
            return None
        return {
            'preClose': prev_close if prev_close > 0 else None,
            'prices': prices,
            'avgPrices': avg_prices,
        }

    results = {}
    last_prices_tuple = None
    duplicate_count = 0
    for market, code in market_codes:
        try:
            time.sleep(0.05)   # 协议缓冲冷却 50ms
            with _api_lock:
                raw = api.get_security_bars(8, market, code, 0, 240)
            sp = _process_bars(raw, prev_closes.get(code) or 0)
            if sp is None:
                continue

            # 串包检测：完整 prices 序列与上一只票完全相同 → 100% 是 pytdx 缓冲污染
            cur_tuple = tuple(sp['prices'])
            if last_prices_tuple is not None and cur_tuple == last_prices_tuple:
                duplicate_count += 1
                logger.warning(
                    f'⚠ TDX 分时串包：{code} prices 序列与上一只票完全相同（{len(cur_tuple)} 根）→ 重连'
                )
                fresh_api = _force_reconnect()
                if fresh_api is not None:
                    api = fresh_api
                    time.sleep(0.05)
                    with _api_lock:
                        raw = api.get_security_bars(8, market, code, 0, 240)
                    sp = _process_bars(raw, prev_closes.get(code) or 0)
                    if sp is None:
                        continue
                    cur_tuple = tuple(sp['prices'])

            last_prices_tuple = cur_tuple
            results[code] = sp
        except Exception as e:
            logger.warning(f'TDX 分时 {code} 失败：{e}')

    logger.info(f'分时 batch 拿到 {len(results)}/{len(market_codes)} 只'
                + (f'，串包重连 {duplicate_count} 次' if duplicate_count else ''))
    return results


def get_batch_quotes(codes):
    """
    管理员专用：自选/持仓的实时行情。
    混合模式：TDX 实时拿价格类字段（30s 高频），EM 缓存 24h 拿基本面字段（每天 1 次）。
    返回格式与 services.quote_service.get_batch_quotes 一致，前端无需改动。
    """
    if not codes:
        return {}

    # === 第 1 步：TDX 拿实时价格（批 50 只一次）===
    api = _ensure_api()
    market_codes = []
    for code in codes:
        m = _detect_market(code)
        if m >= 0:
            market_codes.append((m, str(code).strip()))

    tdx_q = {}
    if api is not None:
        for i in range(0, len(market_codes), 50):
            batch = market_codes[i:i + 50]
            try:
                with _api_lock:
                    quotes = api.get_security_quotes(batch)
                for q in (quotes or []):
                    c = q.get('code')
                    if c:
                        tdx_q[c] = q
            except Exception as e:
                logger.warning(f'TDX 批量 quote 失败：{e}')

    # === 第 2 步：基本面字段缓存（24h TTL）===
    # EM 拉的字段里，name / pe / industry / ytdPct / mainNetInflow / marketCap / turnoverRate /
    # volRatio / speedPct 这些"基本面 + 派生"字段都缓存 24h，今天不会再打 EM
    import db
    from api_endpoints import eastmoney
    from services.quote_service import code_to_secid, _f, _s

    CACHE_KEY = 'tdx_quote_static:'
    TTL_24H_SECONDS = 24 * 3600

    static_q = {}
    need_em = []
    now = time.time()
    for code in codes:
        cached, updated_at = db.get_cache(f'{CACHE_KEY}{code}')
        if cached and updated_at and (now - updated_at) < TTL_24H_SECONDS:
            static_q[code] = cached
        else:
            need_em.append(code)

    # === 第 3 步：缺基本面的从 EM 拉一次（一天最多一次）===
    if need_em:
        try:
            secids = [code_to_secid(c) for c in need_em]
            secids = [s for s in secids if s]
            raw = eastmoney.raw_em_batch_quote(secids)
            diff = (raw.get('data') or {}).get('diff') or []
            for item in diff:
                c = str(item.get('f12') or '').strip()
                if not c:
                    continue
                fund = {
                    'name':          _s(item.get('f14')),
                    'pe':            _f(item.get('f9')),
                    'industry':      _s(item.get('f100')),
                    'ytdPct':        _f(item.get('f25')),
                    'mainNetInflow': _f(item.get('f62')),
                    # 派生字段需要的"参考时刻快照"，下面合并时按 TDX 实时 price/volume 重算
                    '_ref_price':       _f(item.get('f2')),
                    '_ref_marketCap':   _f(item.get('f20')),
                    '_ref_volume':      _f(item.get('f5')),
                    '_ref_turnoverRate':_f(item.get('f8')),
                    '_ref_volRatio':    _f(item.get('f10')),
                    '_ref_speedPct':    _f(item.get('f22')),
                    '_ref_amplitude':   _f(item.get('f7')),
                }
                static_q[c] = fund
                db.set_cache(f'{CACHE_KEY}{c}', fund)
            logger.info(f'EM 基本面拉取 {len(need_em)} 只，缓存 24h')
        except Exception as e:
            logger.warning(f'EM 基本面拉取失败：{e}')

    # === 第 4 步：合并 TDX 实时 + EM 缓存基本面 ===
    results = {}
    for code in codes:
        tq = tdx_q.get(code)
        sq = static_q.get(code, {})
        # 没数据就跳过
        if not tq and not sq:
            continue

        # 基础组装：固定字段从缓存
        merged = {
            'name':          sq.get('name', ''),
            'pe':            sq.get('pe'),
            'industry':      sq.get('industry', ''),
            'ytdPct':        sq.get('ytdPct'),
            'mainNetInflow': sq.get('mainNetInflow'),
            # 派生字段先用缓存值兜底，下面如果 TDX 有数据会重算
            'turnoverRate':  sq.get('_ref_turnoverRate'),
            'volRatio':      sq.get('_ref_volRatio'),
            'speedPct':      sq.get('_ref_speedPct'),
            'marketCap':     sq.get('_ref_marketCap'),
            'amplitude':     sq.get('_ref_amplitude'),
        }

        if tq:
            price = float(tq.get('price') or 0)
            last_close = float(tq.get('last_close') or 0)
            high = float(tq.get('high') or 0)
            low  = float(tq.get('low') or 0)
            volume = float(tq.get('vol') or 0)
            amount = float(tq.get('amount') or 0)

            change_val = (price - last_close) if (price > 0 and last_close > 0) else None
            change_pct = ((change_val / last_close) * 100) if (change_val is not None and last_close > 0) else None
            amplitude_now = (((high - low) / last_close) * 100) if (high > 0 and low > 0 and last_close > 0) else None

            # 派生字段：用 TDX 实时数据重算
            ref_price = sq.get('_ref_price')
            ref_market_cap = sq.get('_ref_marketCap')
            if ref_price and ref_market_cap and ref_price > 0 and price > 0:
                total_shares = ref_market_cap / ref_price
                merged['marketCap'] = total_shares * price

            ref_volume = sq.get('_ref_volume')
            ref_turnover = sq.get('_ref_turnoverRate')
            if ref_volume and ref_turnover and ref_volume > 0 and volume > 0:
                # 流通股本恒定假设：turnover ∝ volume
                merged['turnoverRate'] = ref_turnover * (volume / ref_volume)

            merged.update({
                'price':      price if price > 0 else None,
                'prevClose':  last_close if last_close > 0 else None,
                'changeVal':  change_val,
                'changePct':  change_pct,
                'volume':     volume if volume > 0 else None,
                'amount':     amount if amount > 0 else None,
                'amplitude':  amplitude_now if amplitude_now is not None else merged['amplitude'],
            })

        results[code] = merged

    return results


def warmup():
    """后台预热：admin 解锁时调用，连接在用户首次扫描/看 K 线之前就准备好。
    阻塞调用，调用方应在独立线程里跑（不要阻塞 API 响应）。"""
    try:
        if _ensure_api() is not None:
            logger.warning('TDX 预热完成')
    except Exception as e:
        logger.warning(f'TDX 预热失败：{e}')

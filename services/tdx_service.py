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
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter('[%(asctime)s] [TDX] %(message)s', datefmt='%H:%M:%S'))
    logger.addHandler(_h)
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
            return _fetch_minute_today(api, market, code)
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


def is_available():
    """探测当前 TDX 是否可用（admin 解锁 UI 上可显示连接状态）。"""
    return _ensure_api() is not None


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

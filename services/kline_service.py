"""
K 线 / 分时服务。

支持 timeframe: '分时', '5日', '日K', '周K', '月K', '年K'
- 分时  → 腾讯分钟接口
- 5日   → 新浪 5 分钟 K 线
- 日/周/月/年 → 东方财富优先（含真实成交额），失败回退腾讯 newfqkline
"""
import datetime
from http_client import fetch_json, FetchError
import symbols


def get_kline(index_name, timeframe):
    """指数 K 线（按显示名查 symbols.CORE_INDICES）。"""
    short_code = symbols.get_short_code(index_name) or 'sh000001'

    if timeframe == '分时':
        return _fetch_minute(short_code)
    elif timeframe == '5日':
        return _fetch_5day(short_code)
    else:
        return _fetch_candle(index_name, short_code, timeframe)


def get_stock_kline(code, timeframe):
    """
    个股 K 线。
    Args:
        code:      6 位股票代码（如 '600519'）
        timeframe: 同 get_kline，'分时'/'5日'/'日K'/'周K'/'月K'/'年K'
    """
    code = (code or '').strip()
    if not code:
        return []
    short_code = symbols.stock_short_code(code)            # sh600519 / sz000001 / bj430047
    em_secid   = symbols.stock_eastmoney_secid(code)        # 1.600519 / 0.000001

    if timeframe == '分时':
        return _fetch_minute(short_code)
    if timeframe == '5日':
        return _fetch_5day(short_code)
    return _fetch_candle_by_secid(em_secid, short_code, timeframe)


# ---------------- 分时 ---------------- #
def _fetch_minute(symbol):
    url = f"http://ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
    res = fetch_json(url)
    data_arr = res['data'][symbol]['data']['data']
    qt_arr = res['data'][symbol]['qt'][symbol]
    prev_close = float(qt_arr[4])
    today = datetime.date.today().strftime("%Y-%m-%d")

    results = []
    prev_cum_vol = 0
    prev_cum_amt = 0
    for line in data_arr:
        # 沪深: "0930 4043.38 5351200 9548685548.50"（含成交额）
        # 北证: "0930 1341.72 60232"（无成交额）
        parts = line.split(' ')
        if len(parts) < 2:
            continue
        time_str = parts[0]
        price = float(parts[1])

        cum_vol = float(parts[2]) / 10000 if len(parts) > 2 else 0
        cum_amt = float(parts[3]) / 100000000 if len(parts) > 3 else 0

        vol = cum_vol - prev_cum_vol if prev_cum_vol else cum_vol
        amt = cum_amt - prev_cum_amt if prev_cum_amt else cum_amt
        prev_cum_vol = cum_vol
        prev_cum_amt = cum_amt

        dt_str = f"{today} {time_str[0:2]}:{time_str[2:4]}:00+0800"
        dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S%z")

        chg = price - prev_close
        pct = (chg / prev_close) * 100 if prev_close else 0

        results.append({
            "time": int(dt.timestamp()),
            "value": price,
            "vol": vol,
            "amt": amt if amt else None,
            "chg": chg,
            "pct": pct,
        })
    return results


# ---------------- 5 日 ---------------- #
def _fetch_5day(symbol):
    url = (
        f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=5&ma=no&datalen=240"
    )
    res = fetch_json(url)

    results = []
    prev_close = None
    for row in res:
        dt_str = f"{row['day']}+0800"
        dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S%z")

        op = float(row['open'])
        cl = float(row['close'])
        hi = float(row['high'])
        lo = float(row['low'])
        vol = float(row['volume']) / 1000000  # 股 → 万手

        if prev_close is None:
            prev_close = op

        chg = cl - prev_close
        pct = (chg / prev_close) * 100 if prev_close else 0
        amp = ((hi - lo) / prev_close) * 100 if prev_close else 0

        results.append({
            "time": int(dt.timestamp()),
            "open": op, "high": hi, "low": lo, "close": cl,
            "value": cl,
            "vol": vol,
            "chg": chg, "pct": pct, "amp": amp,
            "amt": (cl * vol / 100),  # 粗略估算：成交量(万手) * 均价 / 100 = 亿
        })
        prev_close = cl
    return results


# ---------------- 日/周/月/年 K ---------------- #
KLT_MAP = {'日K': 101, '周K': 102, '月K': 103, '年K': 106}
TENCENT_TF_MAP = {'日K': 'day', '周K': 'week', '月K': 'month', '年K': 'year'}


def _fetch_candle(index_name, symbol, timeframe):
    # 优先走东方财富（含真实成交额）
    secid = symbols.get_eastmoney_secid(index_name)
    em_data = _try_eastmoney_by_secid(secid, timeframe) if secid else None
    if em_data:
        return em_data
    # 回退到腾讯
    return _try_tencent(symbol, timeframe)


def _fetch_candle_by_secid(secid, symbol, timeframe):
    """个股版 K 线：直接给 secid 抓 EM，回退腾讯。"""
    em_data = _try_eastmoney_by_secid(secid, timeframe)
    if em_data:
        return em_data
    return _try_tencent(symbol, timeframe)


def _try_eastmoney_by_secid(secid, timeframe):
    """给定 secid 走 EM 历史 K 线接口。"""
    if not secid:
        return None
    klt = KLT_MAP.get(timeframe, 101)
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}"
        f"&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        f"&klt={klt}&fqt=1&beg=0&end=20500101&lmt=300"
    )
    try:
        res = fetch_json(url, headers={'Referer': 'https://quote.eastmoney.com/'})
    except FetchError:
        return None

    klines = res.get('data', {}).get('klines') or []
    if not klines:
        return None

    results = []
    prev_close = None
    for line in klines:
        # "2026-04-17,open,close,high,low,vol(股),amt(元),amp%"
        parts = line.split(',')
        op = float(parts[1])
        cl = float(parts[2])
        hi = float(parts[3])
        lo = float(parts[4])
        vol = float(parts[5]) / 10000         # 手 → 万手
        amt = float(parts[6]) / 100000000     # 元 → 亿

        if prev_close is None:
            prev_close = op
        chg = cl - prev_close
        pct = (chg / prev_close) * 100 if prev_close else 0
        amp = float(parts[7]) if parts[7] not in ('-', '') else 0.0

        results.append({
            "time": parts[0],
            "open": op, "close": cl, "high": hi, "low": lo,
            "vol": vol, "amt": amt, "chg": chg, "pct": pct, "amp": amp,
        })
        prev_close = cl
    return results


def _try_tencent(symbol, timeframe):
    tf_key = TENCENT_TF_MAP.get(timeframe, 'day')
    url = (
        f"http://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
        f"?param={symbol},{tf_key},,,300,qfq"
    )
    try:
        res = fetch_json(url)
    except FetchError:
        return []

    data_arr = res.get('data', {}).get(symbol, {}).get(tf_key, [])
    results = []
    prev_close = None
    for row in data_arr:
        op = float(row[1]); cl = float(row[2])
        hi = float(row[3]); lo = float(row[4])
        vol = float(row[5]) / 10000 if len(row) > 5 else 0
        # field[8] = 成交额(万元) → 亿
        amt = (float(row[8]) / 10000
               if len(row) > 8 and row[8] not in ('', '0.00', '0') else None)
        # field[7] = 振幅%
        amp_raw = row[7] if len(row) > 7 else None

        if prev_close is None:
            prev_close = op
        chg = cl - prev_close
        pct = (chg / prev_close) * 100 if prev_close else 0
        amp = (float(amp_raw) if amp_raw and amp_raw not in ('', '0.00')
               else ((hi - lo) / prev_close * 100 if prev_close else 0))

        results.append({
            "time": row[0],
            "open": op, "close": cl, "high": hi, "low": lo,
            "vol": vol, "amt": amt, "chg": chg, "pct": pct, "amp": amp,
        })
        prev_close = cl
    return results

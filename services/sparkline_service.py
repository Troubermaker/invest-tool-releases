"""
迷你分时图数据服务。

为每只股票提供 当日分钟级价格序列（~240 点），用于前端画 SVG sparkline。

策略：
- 单股按 code 独立缓存（key: "sparkline:XXX"），复用 is_market_cache_stale TTL
- 批量调用内部循环（EM trends2 接口不支持多 secid 并查）
- 按股串行走 RateLimiter，盘中 10 只股约 3.5s，之后缓存命中近即时
"""
import logging

import db
from api_endpoints import eastmoney
from services.quote_service import code_to_secid

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "sparkline_v3:"  # v3: 加 minutes 数组（跳过午休 + 占位）让前端按真实时间画 X 轴


def _parse_trading_minute(time_str):
    """
    把 EM trends2 的时间字符串（'YYYY-MM-DD HH:MM' 或 'HH:MM'）解析成"距 9:30 的交易分钟数"。
    9:30 = 0, 11:29 = 119, 13:00 = 120, 14:59 = 239。
    返回 None 表示该 entry 不在交易时段（午休占位等），调用方应跳过这个点。
    """
    if not time_str or not isinstance(time_str, str):
        return None
    # 取末尾 HH:MM 部分
    hm = time_str.strip().split(' ')[-1]
    parts = hm.split(':')
    if len(parts) < 2:
        return None
    try:
        h = int(parts[0]); m = int(parts[1])
    except ValueError:
        return None
    total = h * 60 + m
    # 上午段 9:30 - 11:29 → 0..119
    if 9 * 60 + 30 <= total <= 11 * 60 + 29:
        return total - (9 * 60 + 30)
    # 下午段 13:00 - 14:59 → 120..239
    if 13 * 60 <= total <= 14 * 60 + 59:
        return 120 + (total - 13 * 60)
    # 11:30 收盘 / 15:00 收盘 一般也归到尾部（容错）
    if total == 11 * 60 + 30:
        return 119
    if total == 15 * 60:
        return 239
    return None   # 午休 11:31-12:59 或盘外


def get_batch_sparklines(codes):
    """
    返回 {code: {preClose, prices}}，无法查询的 code 不在返回中。
    prices 是当日分钟价序列（float 数组）。
    """
    if not codes:
        return {}
    results = {}
    for code in codes:
        try:
            sp = _get_one_sparkline(code)
            if sp:
                results[code] = sp
        except Exception as e:
            logger.warning(f"sparkline 失败 code={code}: {e}")
    return results


def _get_one_sparkline(code):
    """单股分时（带缓存）"""
    code = str(code or '').strip()
    if not code:
        return None

    cache_key = f"{CACHE_KEY_PREFIX}{code}"
    cached, updated_at = db.get_cache(cache_key)
    if cached and not db.is_market_cache_stale(updated_at):
        return cached

    secid = code_to_secid(code)
    if not secid:
        return None

    raw = eastmoney.raw_em_trends2(secid)
    data = (raw or {}).get('data') or {}
    trends = data.get('trends') or []

    prices = []
    avg_prices = []
    minutes = []     # 跟 prices 等长：每个点距 9:30 的"交易分钟数"（0-239，跳过午休）
    for item in trends:
        if not isinstance(item, str):
            continue
        parts = item.split(',')
        if len(parts) < 2:
            continue
        # 第 1 字段是时间，跳过午休占位 + 盘外
        mm = _parse_trading_minute(parts[0])
        if mm is None:
            continue
        try:
            price = float(parts[1])
        except ValueError:
            continue
        if price <= 0:
            continue
        prices.append(price)
        minutes.append(mm)
        # 第 4 字段是均价（某些老接口可能没有，用当前价兜底确保两数组等长）
        if len(parts) >= 4:
            try:
                avg_prices.append(float(parts[3]))
                continue
            except ValueError:
                pass
        avg_prices.append(price)

    if not prices:
        return None

    result = {
        "preClose": _f(data.get('preClose')),
        "prices": prices,
        "avgPrices": avg_prices,
        "minutes":   minutes,   # 0-239 trading-minute offsets, parallel to prices/avgPrices
    }
    db.set_cache(cache_key, result)
    return result


def _f(v):
    if v is None or v == '' or v == '-':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

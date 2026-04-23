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

CACHE_KEY_PREFIX = "sparkline_v2:"  # v2: schema 加了 avgPrices，改 key 让旧缓存自动失效


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
    for item in trends:
        if not isinstance(item, str):
            continue
        parts = item.split(',')
        if len(parts) < 2:
            continue
        try:
            price = float(parts[1])
        except ValueError:
            continue
        prices.append(price)
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

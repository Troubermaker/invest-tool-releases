"""
涨跌池聚合服务（连板 / 涨停 / 炸板 / 冲刺涨停 / 跌停）。

5 个 THS data center 接口返回的 stock 字段名各异，但共有一组 base 字段
（code/name/latest/change_rate/turnover_rate/...）。本服务把它们归一成
统一 schema，并按用户要求的固定顺序输出。

输出 schema：
{
    "pools": [
        {
            "key":   'continuous' | 'limitUp' | 'broken' | 'sprint' | 'limitDown',
            "label": '连板池' | '涨停池' | '炸板池' | '冲刺涨停' | '跌停池',
            "count": int,
            "stocks": [
                {
                    "code": str, "name": str,
                    "price": float|None,           # 现价
                    "changePct": float|None,       # 涨跌幅 %
                    "turnoverRate": float|None,    # 换手率 %
                    "amount": float|None,          # 成交额（元）
                    "circulationValue": float|None,# 流通市值（元）
                    "reason": str,                 # 涨停原因（concept_type）
                    # 池特异字段（可能为空）：
                    "continuousNum": int|None,     # 连板天数
                    "highDays": str,               # 几天几板，如 '5天3板'
                    "firstLimitTime": str,         # 首次封板时间 'HH:MM'
                    "lastLimitTime": str,          # 最后封板时间 'HH:MM'
                    "openNum": int|None,           # 开板次数
                    "orderAmount": float|None,     # 封单额（元）
                    "limitType": str,              # 一字/T字/普通
                    "brokenTime": str,             # 炸板时间
                }, ...
            ]
        }, ...
    ]
}
"""
import logging
from datetime import datetime

import db
from api_endpoints import tonghuashun

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = 'limit_pools'
SINGLE_CACHE_KEY_PREFIX = 'limit_pool'

# 顺序固定：连板 → 涨停 → 炸板 → 冲刺 → 跌停
_POOL_DEFS = [
    ('continuous', '连板池',  tonghuashun.raw_ths_continuous_limit_pool),
    ('limitUp',    '涨停池',  tonghuashun.raw_ths_limit_up_pool),
    ('broken',     '炸板池',  tonghuashun.raw_ths_open_limit_pool),
    ('sprint',     '冲刺涨停', tonghuashun.raw_ths_limit_up_sprint),
    ('limitDown',  '跌停池',  tonghuashun.raw_ths_lower_limit_pool),
]

_POOL_INDEX = {key: (label, fetcher) for key, label, fetcher in _POOL_DEFS}


def get_pool(pool_key, date=None, force=False):
    """单个池子查询，前端按需加载用。返回 {key, label, count, stocks: [...]}"""
    if pool_key not in _POOL_INDEX:
        raise ValueError(f"未知池: {pool_key}")
    label, fetcher = _POOL_INDEX[pool_key]

    if date:
        target_date_str = date.replace('-', '')
        cache_key = f"{SINGLE_CACHE_KEY_PREFIX}:{pool_key}:{target_date_str}"
        is_historical = True
    else:
        target_date = db.current_trading_day(datetime.now())
        target_date_str = target_date.strftime('%Y%m%d')
        cache_key = f"{SINGLE_CACHE_KEY_PREFIX}:{pool_key}"
        is_historical = False

    if not force:
        cached, updated_at = db.get_cache(cache_key)
        if cached and (is_historical or not db.is_market_cache_stale(updated_at, trading_ttl=30)):
            return cached
    else:
        cached, _ = db.get_cache(cache_key)

    try:
        raw = fetcher(date=target_date_str)
    except Exception as e:
        logger.warning(f"{label} 接口异常: {e}")
        return cached if cached else {"key": pool_key, "label": label, "count": 0, "stocks": []}

    status = raw.get('status_code', 0)
    msg = raw.get('status_msg') or ''
    if status not in (0, '0', None):
        if status == 10003 or '集合竞价' in msg:
            logger.info(f"{label} 集合竞价时段拒绝（status={status}），返回缓存")
            return cached if cached else {"key": pool_key, "label": label, "count": 0, "stocks": []}
        logger.warning(f"{label} status={status} msg={msg}")
        return cached if cached else {"key": pool_key, "label": label, "count": 0, "stocks": []}

    info = (raw.get('data') or {}).get('info') or []
    stocks = [_normalize(item) for item in info if isinstance(item, dict) and item.get('code')]

    result = {"key": pool_key, "label": label, "count": len(stocks), "stocks": stocks}

    # 空结果不写缓存（节假日 / 接口抽风），保留上次好缓存
    if not stocks:
        logger.info(f"{label} 返回空，保留上次缓存")
        return cached if cached else result

    db.set_cache(cache_key, result)
    return result


def get_pools(date=None, force=False):
    """
    返回 5 个池子聚合结果，按固定顺序。
    Args:
        date:  'YYYY-MM-DD' 历史日期；None 表示用最近一个交易日
        force: 跳过缓存
    """
    if date:
        target_date_str = date.replace('-', '')
        cache_key = f"{CACHE_KEY_PREFIX}:{target_date_str}"
        is_historical = True
    else:
        target_date = db.current_trading_day(datetime.now())
        target_date_str = target_date.strftime('%Y%m%d')
        cache_key = CACHE_KEY_PREFIX
        is_historical = False

    if not force:
        cached, updated_at = db.get_cache(cache_key)
        if cached and (is_historical or not db.is_market_cache_stale(updated_at, trading_ttl=30)):
            return cached
    else:
        cached, _ = db.get_cache(cache_key)

    pools = []
    any_data = False
    for key, label, fetcher in _POOL_DEFS:
        try:
            raw = fetcher(date=target_date_str)
        except Exception as e:
            logger.warning(f"{label} 接口异常: {e}")
            pools.append({"key": key, "label": label, "count": 0, "stocks": []})
            continue

        status = raw.get('status_code', 0)
        msg = raw.get('status_msg') or ''
        if status not in (0, '0', None):
            # 集合竞价时段 THS 拒绝服务，整体降级返缓存
            if status == 10003 or '集合竞价' in msg:
                logger.info(f"{label} 集合竞价时段拒绝（status={status}），返回缓存")
                return cached if cached else {"pools": _empty_pools()}
            logger.warning(f"{label} status={status} msg={msg}")
            pools.append({"key": key, "label": label, "count": 0, "stocks": []})
            continue

        info = (raw.get('data') or {}).get('info') or []
        stocks = [_normalize(item) for item in info if isinstance(item, dict) and item.get('code')]
        if stocks:
            any_data = True
        pools.append({
            "key":   key,
            "label": label,
            "count": len(stocks),
            "stocks": stocks,
        })

    result = {"pools": pools}

    # 全空（节假日 / 接口集体抽风）：保留上次好缓存，避免覆盖
    if not any_data:
        logger.info("涨跌池全部为空，保留上次缓存")
        return cached if cached else result

    db.set_cache(cache_key, result)
    return result


def _empty_pools():
    return [{"key": k, "label": l, "count": 0, "stocks": []} for k, l, _ in _POOL_DEFS]


def _to_float(v):
    if v is None or v == '' or v == '-':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v):
    f = _to_float(v)
    return int(f) if f is not None else None


def _fmt_time(v):
    """THS 把封板时间存成 'HHMMSS' 数字串或 'HH:MM:SS'。统一成 'HH:MM'。"""
    if not v:
        return ''
    s = str(v).strip()
    if not s or s == '0':
        return ''
    if ':' in s:
        # 已是 HH:MM[:SS]
        return s[:5]
    if s.isdigit() and len(s) >= 4:
        # 'HHMMSS' or 'HHMM'
        return f"{s[:2]}:{s[2:4]}"
    return s


def _normalize(item: dict) -> dict:
    """把 THS 各 pool 接口的字段名归一。所有字段都做 None-safe。"""
    return {
        "code":             str(item.get('code') or ''),
        "name":             str(item.get('name') or ''),
        "price":            _to_float(item.get('latest') or item.get('last_price')),
        "changePct":        _to_float(item.get('change_rate') or item.get('changes')),
        "turnoverRate":     _to_float(item.get('turnover_rate')),
        "amount":           _to_float(item.get('amount') or item.get('turnover')),
        "circulationValue": _to_float(item.get('currency_value') or item.get('circulation_value')),
        "reason":           str(item.get('reason_type') or ''),
        # 池特异字段（不存在则为 None / ''）
        "continuousNum":    _to_int(item.get('continuous_num')),
        "highDays":         str(item.get('high_days') or ''),
        "firstLimitTime":   _fmt_time(item.get('first_limit_up_time')),
        "lastLimitTime":    _fmt_time(item.get('last_limit_up_time')),
        "openNum":          _to_int(item.get('open_num')),
        "orderAmount":      _to_float(item.get('order_amount')),
        "limitType":        str(item.get('limit_up_type') or item.get('limit_down_type') or ''),
        "brokenTime":       _fmt_time(item.get('broken_time') or item.get('last_time')),
    }

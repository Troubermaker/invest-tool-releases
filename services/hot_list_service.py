"""
同花顺热榜服务。

提供 A 股热榜数据，支持两个时间窗口：
    - 'hour'  → 近 1 小时热榜
    - 'day'   → 近 24 小时热榜

输出 schema：[{rank, code, name, changePct, rankChange, concepts, popularityTag, hotScore}, ...]
"""
import logging

import db
from api_endpoints import tonghuashun

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = 'ths_hot_list:'


def get_hot_list(period='hour', force=False):
    if period not in ('hour', 'day'):
        raise ValueError(f"period 必须是 'hour' 或 'day'，收到: {period}")

    cache_key = f"{CACHE_KEY_PREFIX}{period}"
    cached, updated_at = db.get_cache(cache_key)
    # 热榜变化不算频繁，盘中 60s TTL 足够
    if not force and cached and not db.is_market_cache_stale(updated_at, trading_ttl=60):
        return cached

    raw = (tonghuashun.raw_ths_hot_list_hour() if period == 'hour'
           else tonghuashun.raw_ths_hot_list_day())

    items = (raw.get('data') or {}).get('stock_list') or []
    results = []
    for item in items:
        if not isinstance(item, dict) or not item.get('code'):
            continue
        tag = item.get('tag') or {}
        results.append({
            'rank':          int(item.get('order') or 0),
            'code':          str(item.get('code') or ''),
            'name':          str(item.get('name') or ''),
            'changePct':     float(item.get('rise_and_fall') or 0),
            'rankChange':    int(item.get('hot_rank_chg') or 0),
            'concepts':      list(tag.get('concept_tag') or []),
            'popularityTag': str(tag.get('popularity_tag') or ''),
            'hotScore':      float(item.get('rate') or 0),
        })

    # 空结果不污染缓存（接口异常时保留上次的好数据）
    if not results:
        logger.info(f"THS 热榜 {period} 返回空，保留上次缓存")
        return cached if cached else []

    db.set_cache(cache_key, results)
    return results

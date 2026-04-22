"""
连板天梯服务。

同花顺 continuous_limit_up 接口返回按连板高度分组的股票榜：
    - 最高板数在最上层（如 6 连板）
    - 向下递减到 2 连板（首板不含在 ladder 里）

输出 schema（适合前端直接渲染阶梯列表）：
    [
        { "height": 6, "label": "6 连板", "number": 2, "stocks": [{code, name}, ...] },
        { "height": 5, "label": "5 连板", "number": 3, "stocks": [...] },
        ...
    ]
"""
import logging

import db
from api_endpoints import tonghuashun

logger = logging.getLogger(__name__)

CACHE_KEY = "limit_up_ladder"


def get_ladder():
    """返回按连板高度降序的天梯数据。"""
    cached, updated_at = db.get_cache(CACHE_KEY)
    if cached and not db.is_market_cache_stale(updated_at):
        return cached

    raw = tonghuashun.raw_ths_continuous_limit_up()
    if raw.get('status_code', 0) not in (0, '0', None):
        raise RuntimeError(f"THS 连板天梯接口错误 status_code={raw.get('status_code')} msg={raw.get('status_msg')}")

    data_list = raw.get('data') or []
    results = []
    for tier in data_list:
        if not isinstance(tier, dict):
            continue
        height = int(tier.get('height') or 0)
        if height < 2:
            continue  # 首板太多不放天梯
        code_list = tier.get('code_list') or []
        stocks = [
            {"code": str(s.get('code', '')), "name": s.get('name', '')}
            for s in code_list
            if isinstance(s, dict) and s.get('code')
        ]
        results.append({
            "height": height,
            "label": f"{height} 连板",
            "number": tier.get('number') or len(stocks),
            "stocks": stocks,
        })

    # 按连板高度降序（最高板在最上）
    results.sort(key=lambda t: -t['height'])

    db.set_cache(CACHE_KEY, results)
    return results

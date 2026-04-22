"""
连板天梯服务。

合并两个同花顺接口：
    1. continuous_limit_up  —— 提供"按连板高度分组"的结构
    2. limit_up_pool        —— 提供每只股票的 reason_type（概念/所属板块）

输出 schema：
    [
        { "height": 6, "label": "6 连板", "number": 2, "stocks": [{code, name, reason, reasonAll}, ...] },
        ...
    ]
    - reason    : 首个概念（如 "煤制烯烃"），前端徽章主显
    - reasonAll : 完整概念串（如 "煤制烯烃+一季报增长+青岛国资"），前端 hover 气泡全显
"""
import logging

import db
from api_endpoints import tonghuashun

logger = logging.getLogger(__name__)

CACHE_KEY = "limit_up_ladder"


def get_ladder():
    """返回按连板高度降序的天梯数据，每只股票带概念标签。"""
    cached, updated_at = db.get_cache(CACHE_KEY)
    if cached and not db.is_market_cache_stale(updated_at):
        return cached

    # —— 1. 拉连板天梯（主结构）—— #
    ladder_raw = tonghuashun.raw_ths_continuous_limit_up()
    if ladder_raw.get('status_code', 0) not in (0, '0', None):
        raise RuntimeError(
            f"THS 连板天梯接口错误 status_code={ladder_raw.get('status_code')} "
            f"msg={ladder_raw.get('status_msg')}"
        )

    # —— 2. 拉涨停池（拿 reason_type 做 code → 概念 映射）—— #
    reason_map = {}
    try:
        pool_raw = tonghuashun.raw_ths_limit_up_pool()
        pool_info = (pool_raw.get('data') or {}).get('info') or []
        for stock in pool_info:
            if isinstance(stock, dict) and stock.get('code'):
                reason_map[str(stock['code'])] = str(stock.get('reason_type') or '')
    except Exception as e:
        logger.warning(f"涨停池拉取失败（天梯概念标签将为空）: {e}")

    # —— 3. 合并 —— #
    data_list = ladder_raw.get('data') or []
    results = []
    for tier in data_list:
        if not isinstance(tier, dict):
            continue
        height = int(tier.get('height') or 0)
        if height < 2:
            continue
        code_list = tier.get('code_list') or []
        stocks = []
        for s in code_list:
            if not isinstance(s, dict) or not s.get('code'):
                continue
            code = str(s['code'])
            reason_all = reason_map.get(code, '')
            # 直接按 '+' 拆成概念列表，无需匹配板块
            concepts = [c.strip() for c in reason_all.split('+') if c.strip()] if reason_all else []
            stocks.append({
                "code": code,
                "name": s.get('name', ''),
                "reasonAll": reason_all,     # 完整概念串（hover 全显）
                "concepts": concepts,        # 纯字符串数组，每个元素是一个涨停原因
            })
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

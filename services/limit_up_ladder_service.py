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
from datetime import datetime

import db
from api_endpoints import tonghuashun

logger = logging.getLogger(__name__)

CACHE_KEY = "limit_up_ladder"


def get_ladder(date=None, force=False):
    """
    返回按连板高度降序的天梯数据，每只股票带概念标签。
    Args:
        date:  'YYYY-MM-DD' 历史日期；None 表示用最近一个交易日
        force: 跳过缓存
    """
    # 决定查询哪天的数据
    if date:
        target_date_str = date.replace('-', '')  # 'YYYY-MM-DD' → 'YYYYMMDD'
        cache_key = f"{CACHE_KEY}:{target_date_str}"
        is_historical = True
    else:
        target_date = db.current_trading_day(datetime.now())
        target_date_str = target_date.strftime('%Y%m%d')
        cache_key = CACHE_KEY
        is_historical = False

    # 缓存检查：历史永久新鲜；实时按 30s TTL
    if not force:
        cached, updated_at = db.get_cache(cache_key)
        if cached and (is_historical or not db.is_market_cache_stale(updated_at, trading_ttl=30)):
            return cached
    else:
        cached, _ = db.get_cache(cache_key)

    # —— 1. 拉连板天梯（主结构）—— #
    ladder_raw = tonghuashun.raw_ths_continuous_limit_up(date=target_date_str)
    status = ladder_raw.get('status_code', 0)
    msg = ladder_raw.get('status_msg') or ''
    if status not in (0, '0', None):
        # 集合竞价时段（9:15-9:25、14:57-15:00）THS 会拒绝返回 status=10003，
        # 这是已知业务行为，不是故障；降级返回上次缓存（若无则空数组），让前端静静地用历史数据即可
        if status == 10003 or '集合竞价' in msg:
            logger.info(
                f"THS 天梯集合竞价时段拒绝服务（status={status} msg={msg}），"
                f"返回{'缓存' if cached else '空列表'}"
            )
            return cached if cached else []
        raise RuntimeError(
            f"THS 连板天梯接口错误 status_code={status} msg={msg}"
        )

    # —— 2. 拉涨停池（拿 reason_type 做 code → 概念 映射），同一天 —— #
    reason_map = {}
    try:
        pool_raw = tonghuashun.raw_ths_limit_up_pool(date=target_date_str)
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

    # 空结果不写缓存：周末 / 节假日 THS 接口会返回空（status=0 但 data=[]），
    # 如果把空写进去就把上一个交易日的好缓存覆盖掉了。
    # 此时返回上次的好缓存（如果有），让用户看到上一个交易日的天梯。
    if not results:
        logger.info("THS 天梯返回空（可能是非交易日），保留上次缓存")
        return cached if cached else []

    db.set_cache(cache_key, results)  # 用动态 cache_key（实时 / 历史区分）
    return results

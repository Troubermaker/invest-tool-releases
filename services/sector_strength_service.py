"""
板块强度服务（Week 1 Day 3-5）。

核心功能：
  1. 计算每个板块的"强度评分"（0-100）
  2. 反向映射：stock_code → 该股所在的最强板块的强度

数据源：复用 sector_service.get_all_sectors 和 sector_stocks_service.get_sector_stocks
（都来自 KPL 数据源）。

板块强度评分组成（0-100）：
  - KPL 内部强度 strength 字段（50%权重）—— KPL 数据源已经基于主力资金算好
  - 板块涨幅排名分位数（30%权重）—— 越靠前分越高
  - 主力净流入金额（20%权重）—— 正流入加分，负流入减分

缓存：盘中 5 分钟 TTL（反向映射要打 30+ 个 KPL 接口，比较重）。
盘后定型，缓存到下个交易日。
"""
import logging
from datetime import datetime

import db
from services import sector_service
from services import sector_stocks_service

logger = logging.getLogger(__name__)

# 板块强度 score 缓存 key（v2: 修复 KPL strength 归一化错误）
_STRENGTHS_CACHE_KEY = 'sector_strengths_v2'
# 反向映射缓存（stock_code → strongest sector score）
_REVERSE_MAP_CACHE_KEY = 'stock_sector_strength_map_v2'
# 反向映射用到的板块数（覆盖度 vs 接口压力的平衡：30 个板块 ≈ 60% A 股票）
_TOP_SECTORS_FOR_MAP = 30


def get_sector_strengths(force=False):
    """
    返回 [{code, name, score, kpl_strength, change_pct, inflow_yi, rank}, ...]，按 score 倒序。

    score 公式（0-100）：
      kpl_strength × 0.50    (KPL 自带强度 0-100)
      rank_pct      × 0.30    (排名分位数，前 5% → 100，最后 5% → 0)
      inflow_score  × 0.20    (主力净流入打分 0-100)
    """
    if not force:
        cached, updated_at = db.get_cache(_STRENGTHS_CACHE_KEY)
        if cached and not db.is_market_cache_stale(updated_at, trading_ttl=300):
            return cached

    # 拉前 80 个板块（KPL 限制）
    try:
        sectors_raw = sector_service.get_all_sectors(force=force, limit=80)
    except Exception as e:
        logger.warning(f'拉板块榜失败：{e}')
        return []
    if not sectors_raw:
        return []

    # KPL strength 不是 0-100（实际 -200 到 12000+），用百分位归一化
    strengths = [s.get('strength', 0) or 0 for s in sectors_raw]
    strengths_sorted = sorted(strengths)
    # 计算每个值在排序里的位置 → 0-100 百分位
    def strength_percentile(v):
        # 简单线性搜索（80 个 sector 无性能问题）
        if not strengths_sorted: return 50.0
        n = len(strengths_sorted)
        idx = sum(1 for x in strengths_sorted if x < v)
        return idx / max(1, n - 1) * 100

    # 计算 inflow_score（净流入金额映射到 0-100；正流入加分多）
    inflows = []
    for s in sectors_raw:
        inf_str = s.get('inflow', '0亿').replace('亿', '').replace('+', '')
        try:
            inflows.append(float(inf_str))
        except ValueError:
            inflows.append(0.0)

    def inflow_to_score(inf_yi, all_infs):
        if not all_infs: return 50.0
        max_inf = max(all_infs)
        min_inf = min(all_infs)
        if inf_yi >= max_inf * 0.5: return 100.0
        if inf_yi <= min_inf * 0.5 and min_inf < 0: return 0.0
        if inf_yi >= 0:
            return 50.0 + (inf_yi / (max_inf or 1)) * 50.0
        else:
            return 50.0 + (inf_yi / (abs(min_inf) or 1)) * 50.0

    total = len(sectors_raw)
    out = []
    for i, s in enumerate(sectors_raw):
        raw_strength = s.get('strength', 0) or 0
        # 百分位归一化到 0-100（替代原来的直接乘以 0.5）
        kpl_strength_pct = strength_percentile(raw_strength)
        # 排名分位数：rank=1 (最强) → 100 分；rank=total → 0 分
        rank_pct_score = max(0.0, (total - i) / total * 100)
        inflow_score = inflow_to_score(inflows[i], inflows)

        # 三个 0-100 分量加权 → 输出 0-100
        score = round(
            kpl_strength_pct * 0.50 +
            rank_pct_score   * 0.30 +
            inflow_score     * 0.20,
            1,
        )
        out.append({
            'code':         s.get('code', ''),
            'name':         s.get('name', ''),
            'rank':         i + 1,
            'kpl_strength': int(raw_strength),     # 保留原值供调试
            'change':       s.get('change', ''),
            'inflow':       s.get('inflow', ''),
            'score':        score,
        })

    db.set_cache(_STRENGTHS_CACHE_KEY, out)
    return out


def get_stock_sector_strength_map(force=False, top_n=_TOP_SECTORS_FOR_MAP):
    """
    反向映射：stock_code → 该股所属的最强板块强度。

    Returns:
        { stock_code: {
            best_sector_code:  '801001',
            best_sector_name:  '芯片',
            best_sector_score: 78.3,
            sector_count:      3,        # 这只票出现在几个 top 板块里
        }, ... }
    """
    if not force:
        cached, updated_at = db.get_cache(_REVERSE_MAP_CACHE_KEY)
        if cached and not db.is_market_cache_stale(updated_at, trading_ttl=300):
            return cached

    sectors = get_sector_strengths(force=force)
    if not sectors:
        return {}

    # 取 top_n 个板块拉股票列表
    sectors_top = sectors[:top_n]

    code_to_sectors = {}
    for sec in sectors_top:
        try:
            stocks = sector_stocks_service.get_sector_stocks(sec['code'])
        except Exception as e:
            logger.warning(f"拉板块{sec['name']}股票失败：{e}")
            continue
        for st in stocks:
            code = st.get('code')
            if not code: continue
            entry = {
                'sector_code':  sec['code'],
                'sector_name':  sec['name'],
                'sector_score': sec['score'],
            }
            code_to_sectors.setdefault(code, []).append(entry)

    # 压缩：每只票保留"最强板块"
    out = {}
    for code, secs in code_to_sectors.items():
        best = max(secs, key=lambda s: s['sector_score'])
        out[code] = {
            'best_sector_code':  best['sector_code'],
            'best_sector_name':  best['sector_name'],
            'best_sector_score': best['sector_score'],
            'sector_count':      len(secs),
        }

    db.set_cache(_REVERSE_MAP_CACHE_KEY, out)
    logger.info(f'sector_strength_map 构建完成：{len(out)} 只票 / {len(sectors_top)} 个板块')
    return out


def get_stock_strength(code, mapping=None):
    """
    单只票的板块强度（便利方法）。
    返回 dict 或 None（票不在 top 板块映射里）。
    """
    if mapping is None:
        mapping = get_stock_sector_strength_map()
    return mapping.get(code)


def get_batch_stock_strengths(codes):
    """
    批量查多只票的板块强度。供 scanner 用。
    Returns: { code: {best_sector_name, best_sector_score, sector_count} | None }
    """
    mapping = get_stock_sector_strength_map()
    return {c: mapping.get(c) for c in codes}

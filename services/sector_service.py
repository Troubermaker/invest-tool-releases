"""
热门板块服务。

数据源优先级：
    1. 开盘啦（KPL）精选板块实时榜 —— 主力资金维度，真正反映"被资金抱团"的题材
    2. 新浪行业板块 —— KPL 失败时降级，基于涨跌幅

上层统一调用 get_hot_sectors()，不用关心源。
输出 schema 两源一致，前端无感知切换。
"""
import json
import logging

from http_client import fetch_text
import db
from api_endpoints import kaipanla

logger = logging.getLogger(__name__)

KPL_CACHE_KEY = "hot_sectors_kpl"
SINA_CACHE_KEY = "hot_sectors"
TOP_N = 20


def get_hot_sectors(date=None, force=False):
    """
    返回 [{rank, name, change, inflow, code, up}, ...]
    Args:
        date: 'YYYY-MM-DD' 历史日期；None 表示当前交易日（实时）
        force: 跳过缓存
    主源 KPL，失败自动降级到新浪（仅实时；历史只走 KPL apphis）。
    """
    if date:
        # 历史路径：只走 KPL apphis，没降级
        return _get_from_kpl_historical(date, force)
    try:
        return _get_from_kpl(force)
    except Exception as e:
        logger.warning(f"KPL 精选板块失败，降级到新浪: {e}")
        return _get_from_sina(force)


def _parse_kpl_list(lst):
    """KPL 板块榜 list 字段解析。实时和历史返回结构一致，复用。"""
    results = []
    for i, item in enumerate(lst[:TOP_N]):
        if not isinstance(item, list) or len(item) < 7:
            continue
        # 字段索引：[代码, 名称, 强度, 涨跌幅%, 领涨股涨幅%, 成交额, 主力净流入...]
        code = str(item[0])
        name = item[1]
        strength = int(item[2])
        change_pct = float(item[3])
        inflow_yuan = float(item[6])
        inflow_yi = inflow_yuan / 100000000
        up = change_pct >= 0
        results.append({
            "rank": i + 1,
            "name": name,
            "change": f"{'+' if up else ''}{change_pct:.2f}%",
            "inflow": f"{'+' if inflow_yi >= 0 else ''}{inflow_yi:.2f}亿",
            "code": code,
            "strength": strength,
            "up": up,
        })
    return results


# ---------------- KPL 主源（实时）---------------- #

def _get_from_kpl(force=False):
    if not force:
        cached, updated_at = db.get_cache(KPL_CACHE_KEY)
        if cached and not db.is_market_cache_stale(updated_at, trading_ttl=15):
            return cached

    raw = kaipanla.raw_kpl_real_ranking()
    lst = raw.get('list') or []
    if not lst:
        raise RuntimeError("KPL 精选板块返回空 list")

    results = _parse_kpl_list(lst)
    if not results:
        raise RuntimeError("KPL 精选板块解析后 0 条")
    db.set_cache(KPL_CACHE_KEY, results)
    return results


# ---------------- KPL 历史（apphis）---------------- #

def _get_from_kpl_historical(date, force=False):
    """
    历史板块榜，date 'YYYY-MM-DD'。
    缓存 key: hot_sectors_kpl:YYYYMMDD，过去数据永久新鲜（不会变）。
    """
    cache_key = f"{KPL_CACHE_KEY}:{date.replace('-', '')}"
    if not force:
        cached, _ = db.get_cache(cache_key)
        if cached:
            return cached  # 历史数据 cache 永久新鲜

    raw = kaipanla.raw_kpl_real_ranking_historical(date)
    lst = raw.get('list') or []
    results = _parse_kpl_list(lst)
    if not results:
        # 空结果不污染缓存（万一 date 是非交易日 / 接口暂时失败）
        return []
    db.set_cache(cache_key, results)
    return results


# ---------------- 新浪降级源 ---------------- #

def _get_from_sina(force=False):
    if not force:
        cached, updated_at = db.get_cache(SINA_CACHE_KEY)
        if cached and not db.is_market_cache_stale(updated_at, trading_ttl=15):
            return cached

    url = "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
    text = fetch_text(url, encoding="gbk")
    raw_json_str = text.split('=', 1)[1].strip().strip(';')
    data_dict = json.loads(raw_json_str)
    items = list(data_dict.values())
    items.sort(key=lambda x: float(x.split(',')[5]), reverse=True)

    results = []
    for i, rowstr in enumerate(items[:TOP_N]):
        cols = rowstr.split(',')
        if len(cols) < 8:
            continue
        name = cols[1]
        change_pct = float(cols[5])
        turnover_amount = float(cols[7]) / 100000000
        up = change_pct >= 0
        results.append({
            "rank": i + 1, "name": name,
            "change": f"{'+' if up else ''}{change_pct:.2f}%",
            "inflow": f"{turnover_amount:.2f}亿",
            "code": cols[0], "strength": 0, "up": up,
        })

    db.set_cache(SINA_CACHE_KEY, results)
    return results

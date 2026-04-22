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


def get_hot_sectors(force=False):
    """
    返回 [{rank, name, change, inflow, code, up}, ...]
    主源 KPL，失败自动降级到新浪。
    """
    try:
        return _get_from_kpl(force)
    except Exception as e:
        logger.warning(f"KPL 精选板块失败，降级到新浪: {e}")
        return _get_from_sina(force)


# ---------------- KPL 主源 ---------------- #

def _get_from_kpl(force=False):
    if not force:
        cached, updated_at = db.get_cache(KPL_CACHE_KEY)
        if cached and not db.is_market_cache_stale(updated_at):
            return cached

    raw = kaipanla.raw_kpl_real_ranking()
    lst = raw.get('list') or []
    if not lst:
        raise RuntimeError("KPL 精选板块返回空 list")

    results = []
    for i, item in enumerate(lst[:TOP_N]):
        if not isinstance(item, list) or len(item) < 7:
            continue
        # 字段索引来自 KPL 协议：[代码, 名称, 强度, 涨跌幅%, 领涨股涨幅%, 成交额, 主力净流入...]
        code = str(item[0])
        name = item[1]
        strength = int(item[2])  # KPL 精选强度分：资金+成交+个股动量综合算出，数值越大越"热"
        change_pct = float(item[3])
        inflow_yuan = float(item[6])
        inflow_yi = inflow_yuan / 100000000  # 元 → 亿
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

    if not results:
        raise RuntimeError("KPL 精选板块解析后 0 条")

    db.set_cache(KPL_CACHE_KEY, results)
    return results


# ---------------- 新浪降级源 ---------------- #

def _get_from_sina(force=False):
    if not force:
        cached, updated_at = db.get_cache(SINA_CACHE_KEY)
        if cached and not db.is_market_cache_stale(updated_at):
            return cached

    url = "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
    text = fetch_text(url, encoding="gbk")

    # 响应格式: var S_Finance_bankuai_sinaindustry = { ... }
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
            "rank": i + 1,
            "name": name,
            "change": f"{'+' if up else ''}{change_pct:.2f}%",
            "inflow": f"{turnover_amount:.2f}亿",
            "code": cols[0],
            "strength": 0,  # Sina 源无强度数据，前端 v-if 判定隐藏
            "up": up,
        })

    db.set_cache(SINA_CACHE_KEY, results)
    return results

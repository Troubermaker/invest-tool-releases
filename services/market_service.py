"""
行情概览服务：核心指数 + 全市场成交额。

数据源：新浪 hq.sinajs.cn 行情接口（GBK，返回 JSONP 样式文本）
"""
from http_client import fetch_text
import db
import symbols


CACHE_KEY = "market_indices"


def get_market_indices(date=None, force=False):
    """
    返回 {"indices": [...], "total_turnover": float}
    - 使用"行情专用"缓存策略：盘中 60s、盘后 1h、跨天失效
    - force=True 时跳过缓存
    - date 历史日期：暂不支持（新浪批量指数接口无历史版），返回 None 让前端隐藏面板
    """
    if date:
        return None  # 历史不支持

    if not force:
        cached, updated_at = db.get_cache(CACHE_KEY)
        # 指数 + 板块榜，前端 15s 轮询，配套 15s 盘中 TTL
        if cached and not db.is_market_cache_stale(updated_at, trading_ttl=15):
            return cached

    codes = symbols.get_sina_quote_codes()
    names_codes = symbols.get_names_with_sina_codes()
    url = f"http://hq.sinajs.cn/list={','.join(codes)}"
    text = fetch_text(
        url,
        encoding="gbk",
        headers={"Referer": "http://finance.sina.com.cn"},
    )

    results = []
    total_amt = 0.0
    for i, line in enumerate(text.strip().split('\n')):
        if not line:
            continue
        # 格式: var hq_str_s_sh000001="上证指数,4055.55,28.34,0.70,5458769,97656549";
        data_str = line.split('=')[1].strip('";')
        cols = data_str.split(',')
        if len(cols) < 6:
            continue

        name, sina_code = names_codes[i]
        price = float(cols[1])
        change_val = float(cols[2])
        change_pct = float(cols[3])
        up = change_pct >= 0
        idx_amt = float(cols[5]) / 10000  # 万元 → 亿

        if sina_code in symbols.TOTAL_TURNOVER_CONTRIBUTORS:
            total_amt += idx_amt

        results.append({
            "name": name,
            "price": f"{price:.2f}",
            "change": f"{'+' if up else ''}{change_pct:.2f}%",
            "value": f"{'+' if up else ''}{change_val:.2f}",
            "up": up,
            "amt": idx_amt,
        })

    if not results:
        raise RuntimeError("新浪行情接口返回空数据")

    result = {"indices": results, "total_turnover": total_amt}
    db.set_cache(CACHE_KEY, result)
    return result

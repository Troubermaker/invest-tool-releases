"""
市场情绪服务：聚合"全市场成交额 + 涨跌分布"两个维度。

数据源：
    - 同花顺 up_down_distribution  → 涨跌家数、涨停跌停、停牌
    - 同花顺 turnover_minute       → 今日成交额、昨日成交额、较昨日变动

输出 schema 直接喂给前端顶部条右侧面板。
"""
import logging

import db
from api_endpoints import tonghuashun

logger = logging.getLogger(__name__)

CACHE_KEY = "market_sentiment"


def get_sentiment(force=False):
    """
    返回 {
        turnover: { today, yesterday, change, changePct, isExpansion },
        breadth:  { up, down, flat, limitUp, limitDown, suspend, upPct, downPct, flatPct },
        updateTime: "YYYY-MM-DD HH:MM:SS"
    }
    所有金额单位：元（前端自行 → 亿/万亿）
    """
    if not force:
        cached, updated_at = db.get_cache(CACHE_KEY)
        if cached and not db.is_market_cache_stale(updated_at):
            return cached

    # 1. 成交额分时（取 header 里的聚合值，point_list 暂不用）
    tm_raw = tonghuashun.raw_ths_turnover_minute()
    tm_header = (tm_raw.get('data', {}) or {}).get('charts', {}).get('header') or []
    turnover_today = _find_header_val(tm_header, 'turnover')
    turnover_pre = _find_header_val(tm_header, 'turnover_pre')
    turnover_change = _find_header_val(tm_header, 'turnover_change')
    change_pct = (turnover_change / turnover_pre * 100) if turnover_pre else 0.0

    # 2. 涨跌分布
    ud_raw = tonghuashun.raw_ths_up_down_distribution()
    ud_data = ud_raw.get('data', {}) or {}
    up = int(ud_data.get('up') or 0)
    down = int(ud_data.get('down') or 0)
    flat = int(ud_data.get('flat') or 0)
    limit_up = int(ud_data.get('limit_up') or 0)
    limit_down = int(ud_data.get('limit_down') or 0)
    suspend = int(ud_data.get('suspend') or 0)
    total = up + down + flat
    up_pct = (up / total * 100) if total else 0.0
    down_pct = (down / total * 100) if total else 0.0
    flat_pct = (flat / total * 100) if total else 0.0

    result = {
        "turnover": {
            "today": turnover_today,
            "yesterday": turnover_pre,
            "change": turnover_change,
            "changePct": round(change_pct, 2),
            "isExpansion": (turnover_change or 0) >= 0,
        },
        "breadth": {
            "up": up,
            "down": down,
            "flat": flat,
            "limitUp": limit_up,
            "limitDown": limit_down,
            "suspend": suspend,
            "upPct": round(up_pct, 2),
            "downPct": round(down_pct, 2),
            "flatPct": round(flat_pct, 2),
        },
        "updateTime": ud_data.get('last_update_time', ''),
    }
    db.set_cache(CACHE_KEY, result)
    return result


def _find_header_val(header_list, key):
    """从 header list 按 key 找 val，找不到返回 0"""
    for item in header_list:
        if isinstance(item, dict) and item.get('key') == key:
            return float(item.get('val') or 0)
    return 0.0

"""
板块联动个股服务。

根据板块 ID（KPL 板块代码，如 '801001' 芯片）返回该板块下的精选股票列表。
数据源：开盘啦 ZhiShuStockList_W8 接口。
"""
import logging
from datetime import datetime

import db
from api_endpoints import kaipanla

logger = logging.getLogger(__name__)

CACHE_PREFIX = "sector_stocks:"


def get_sector_stocks(plate_id, date=None, force=False):
    """
    返回 [{code, name, price, change, ..., leader, streak, theme}, ...]
    Args:
        plate_id: 板块 id（如 '801001'）
        date:     'YYYY-MM-DD' 历史日期；None 表示当前交易日
        force:    跳过缓存
    """
    if not plate_id:
        return []

    # date 是当前交易日就当成实时（KPL apphis 对当天不返回数据）
    today_str = db.current_trading_day(datetime.now()).strftime('%Y-%m-%d')
    is_today = (date is None) or (date == today_str)

    # 缓存 key：实时 = 'sector_stocks:801001'；历史 = 'sector_stocks:801001:20260422'
    if is_today:
        cache_key = f"{CACHE_PREFIX}{plate_id}"
    else:
        cache_key = f"{CACHE_PREFIX}{plate_id}:{date.replace('-', '')}"

    if not force:
        cached, updated_at = db.get_cache(cache_key)
        # 历史缓存永久新鲜；实时按 TTL 判
        if cached and (not is_today or not db.is_market_cache_stale(updated_at)):
            return cached

    # 抓取：实时走 apphq；过去日子走 apphis
    if is_today:
        raw = kaipanla.raw_kpl_plate_stocks(plate_id)
    else:
        raw = kaipanla.raw_kpl_plate_stocks_historical(plate_id, date)

    errcode = raw.get('errcode')
    if errcode is not None and str(errcode) != '0':
        raise RuntimeError(f"KPL 板块股票接口返回错误 errcode={errcode} msg={raw.get('errmsg')}")

    lst = raw.get('list') or []
    results = _parse_stock_list(lst)

    # 按龙头排名排序：龙一→龙二→...→非龙头
    results.sort(key=lambda s: _leader_rank(s['leader']))

    if not results:
        # 空不写缓存（避免污染历史 key）
        return []
    db.set_cache(cache_key, results)
    return results


def _parse_stock_list(lst):
    """KPL ZhiShuStockList_W8 字段解析。实时和历史返回结构一致，复用。"""
    results = []
    for item in lst:
        if not isinstance(item, list) or len(item) < 40:
            continue
        # 字段索引（KPL ZhiShuStockList_W8 协议）:
        # [0] 代码 [1] 名称 [2] 主力类型 [4] 所有题材
        # [5] 最新价 [6] 涨跌幅% [7] 成交额(元)
        # [11] 主力买(元) [12] 主力卖(元,负) [13] 主力净额(元)
        # [23] 连板 [24] 龙头排名 [39] 主打题材
        code = str(item[0])
        name = item[1]
        main_force = str(item[2] or '')
        themes_all = str(item[4] or '')
        price = float(item[5])
        change_pct = float(item[6])
        turnover_yuan = float(item[7])
        main_buy_yuan = float(item[11])
        main_sell_yuan = abs(float(item[12]))
        main_net_yuan = float(item[13])
        streak = str(item[23] or '')
        leader = str(item[24] or '')
        theme = str(item[39] or '')
        up = change_pct >= 0

        results.append({
            "code": code, "name": name,
            "price": f"{price:.2f}",
            "change": f"{'+' if up else ''}{change_pct:.2f}%",
            "turnover": _format_turnover(turnover_yuan),
            "mainBuy": _format_turnover(main_buy_yuan),
            "mainSell": _format_turnover(main_sell_yuan),
            "mainNet": _format_signed(main_net_yuan),
            "mainNetUp": main_net_yuan >= 0,
            "up": up,
            "isLimitUp": _is_limit_up(code, change_pct, name),
            "leader": leader, "streak": streak,
            "theme": theme, "themesAll": themes_all,
            "mainForce": main_force,
        })
    return results


_LEADER_ORDER = {'龙一': 1, '龙二': 2, '龙三': 3, '龙四': 4, '龙五': 5,
                 '龙六': 6, '龙七': 7, '龙八': 8, '龙九': 9}


def _leader_rank(leader_str):
    """龙头字符串 → 排序键。非龙头返回 99 排到最后。"""
    return _LEADER_ORDER.get(leader_str, 99)


def _is_limit_up(code: str, change_pct: float, name: str) -> bool:
    """
    判断是否当日涨停。按 A 股规则：
      1. 新股（'C' / 'N' 前缀）上市首 5 日无涨跌限制 → 不算涨停
      2. 板块限制：北交所 30% / 创业板+科创板 20% / 主板 10%
      3. ST 股 5% 限制 **仅对主板生效**，创业板/科创板 ST 仍用板块规则
      容差 0.1% 避免浮点误差（如 9.97% ≈ 涨停）
    """
    name_str = name or ''
    # 规则 1：新股跳过判定
    if name_str.startswith('C') or name_str.startswith('N'):
        return False

    code = code or ''

    # 规则 2：按板块确定基础涨停线
    if code.startswith(('300', '301', '688')):
        limit = 20.0       # 创业板 / 科创板
    elif code.startswith(('8', '4', '920', '9')):
        limit = 30.0       # 北交所 + 老三板
    else:
        limit = 10.0       # 沪深主板（60x / 000 / 001 / 002 / 003）
        # 规则 3：ST 的 5% 限制只在主板生效
        upper = name_str.upper()
        if upper.startswith('ST') or upper.startswith('*ST'):
            limit = 5.0

    return change_pct >= limit - 0.1


def _format_turnover(yuan):
    """元 → 亿/万 自适应字符串（无符号）"""
    if yuan >= 100000000:
        return f"{yuan / 100000000:.2f}亿"
    if yuan >= 10000:
        return f"{yuan / 10000:.2f}万"
    return f"{yuan:.0f}"


def _format_signed(yuan):
    """元 → 亿/万 自适应字符串（带 +/- 符号，用于主力净额等）"""
    sign = '+' if yuan >= 0 else '-'
    return sign + _format_turnover(abs(yuan))

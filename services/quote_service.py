"""
批量实时行情服务。

给定一批 A 股代码，返回每只股票的实时行情 + 基本面字段：
    最新价 / 涨跌幅% / 涨跌额 / 成交量(手) / 成交额(元)
    换手率% / 量比 / 昨收 / 总市值(元) / 涨速% / YTD% / 所属板块

数据源：东财 push2delay.ulist.np（批量，一次调用可查 200+ 只）
"""
import logging

from api_endpoints import eastmoney

logger = logging.getLogger(__name__)


def code_to_secid(code):
    """
    股票代码 → 东财 secid 格式 'market.code'。
    市场 ID 规则：
      - 6 开头（沪主板 + 科创板 688）→ 1
      - 其它（00/002/003/300/301 深市 + 83/87/88/920/4 北交所）→ 0
    """
    if not code:
        return None
    c = str(code).strip()
    if not c:
        return None
    if c.startswith('6'):
        return f'1.{c}'
    return f'0.{c}'


def get_batch_quotes(codes):
    """
    批量查询实时行情。

    Args:
        codes: 股票代码列表，如 ['600519', '000001', '300750']

    Returns:
        dict: {code: {...quote fields...}}，查不到的 code 不在返回中
    """
    if not codes:
        return {}
    # 去重 + 过滤空
    unique_codes = []
    seen = set()
    for c in codes:
        c = str(c or '').strip()
        if c and c not in seen:
            seen.add(c)
            unique_codes.append(c)
    if not unique_codes:
        return {}

    secids = [code_to_secid(c) for c in unique_codes]
    secids = [s for s in secids if s]

    try:
        raw = eastmoney.raw_em_batch_quote(secids)
    except Exception as e:
        logger.error(f"东财批量行情失败: {e}")
        return {}

    diff = (raw.get('data') or {}).get('diff') or []
    results = {}
    for item in diff:
        code = str(item.get('f12') or '').strip()
        if not code:
            continue
        results[code] = {
            "name": _s(item.get('f14')),
            "price": _f(item.get('f2')),
            "changePct": _f(item.get('f3')),
            "changeVal": _f(item.get('f4')),
            "volume": _f(item.get('f5')),          # 成交量（手）
            "amount": _f(item.get('f6')),          # 成交额（元）
            "amplitude": _f(item.get('f7')),       # 振幅 %
            "turnoverRate": _f(item.get('f8')),    # 换手率 %
            "pe": _f(item.get('f9')),              # 市盈率
            "volRatio": _f(item.get('f10')),       # 量比
            "prevClose": _f(item.get('f18')),
            "marketCap": _f(item.get('f20')),      # 总市值（元）
            "speedPct": _f(item.get('f22')),       # 涨速 %
            "ytdPct": _f(item.get('f25')),         # YTD 涨跌幅 %
            "mainNetInflow": _f(item.get('f62')),  # 主力今日净流入（元）
            "industry": _s(item.get('f100')),      # 所属板块/行业
        }
    return results


def _f(v):
    """安全转 float，失败返 None。EM 的 '-' 占位也处理。"""
    if v is None or v == '-' or v == '':
        return None
    try:
        return float(v)
    except Exception:
        return None


def _s(v):
    """安全转 str，'-' 视为空串。"""
    if v is None or v == '-':
        return ''
    return str(v)

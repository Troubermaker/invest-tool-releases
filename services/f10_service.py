"""
F10 简版服务：拉取个股基本面快照。

数据源：
- push2.eastmoney.com  → 实时数值（PE / PB / ROE / 总市值 / 流通市值 / 行业 / 地域）
- emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey  → 描述性文本（主营 / 简介 / 上市日期）

返回字段尽量字典化、单位统一（市值已转亿、股本已转亿股），失败时该字段返回 None
但整体仍尽量返回部分数据，不让一处失败导致整张卡片空白。
"""
import logging

from http_client import fetch_json, FetchError
import symbols

log = logging.getLogger(__name__)

# 东财 F10 端点统一带 Referer，否则部分接口返回空体
# 真实 SPA 路径：/pc_hsf10/pages/index.html（小写），代码也用小写 sh603300 / sz000001
def _f10_headers(em_short=''):
    em_lower = em_short.lower() if em_short else ''
    return {
        'Referer': f'https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code={em_lower}&color=b',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://emweb.securities.eastmoney.com',
    }


def get_f10(code):
    """
    @param code: 6 位股票代码
    @return: dict 或 None（code 非法）
    """
    code = (code or '').strip()
    if not code:
        return None

    out = {'code': code}

    # ---------------- ① push2 quote：拿数值类 ----------------
    secid = symbols.stock_eastmoney_secid(code)
    if secid:
        try:
            url = (
                'https://push2.eastmoney.com/api/qt/stock/get'
                f'?secid={secid}'
                f'&fields=f43,f57,f58,f60,f102,f116,f117,f127,f162,f164,f165,f167,f168,f173'
            )
            res = fetch_json(url, headers={'Referer': 'https://quote.eastmoney.com/'})
            d = (res or {}).get('data') or {}
            out['name']           = d.get('f58') or None
            out['industry']       = d.get('f127') or None
            out['area']           = d.get('f102') or None
            out['lastPrice']      = _safe_float(d.get('f43'))
            out['prevClose']      = _safe_float(d.get('f60'))
            out['totalMarketCap'] = _to_yi(d.get('f116'))     # 元 → 亿
            out['floatMarketCap'] = _to_yi(d.get('f117'))
            out['totalShares']    = _to_yi(d.get('f164'))     # 股 → 亿股
            out['floatShares']    = _to_yi(d.get('f165'))
            out['peDynamic']      = _safe_float(d.get('f162'))
            out['pb']             = _safe_float(d.get('f167'))
            out['dividendYield']  = _safe_float(d.get('f168'))
            out['roe']            = _safe_float(d.get('f173'))
        except FetchError as e:
            log.warning('[f10] push2 quote 失败 code=%s: %s', code, e)

    # ---------------- ② CompanySurvey：拿描述性文本 ----------------
    short = symbols.stock_short_code(code)
    em_short = short.upper() if short else None  # SH600519 / SZ000001 / BJ430047
    if em_short:
        try:
            url = f'https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={em_short}'
            res = fetch_json(url, headers=_f10_headers(em_short))
            jbzl_list = (res or {}).get('jbzl') or []
            jbzl = jbzl_list[0] if jbzl_list else {}
            gsgk_list = (res or {}).get('gsgk') or []
            gsgk = gsgk_list[0] if gsgk_list else {}

            out['fullName']     = jbzl.get('ORG_NAME') or jbzl.get('SECURITY_NAME_ABBR')
            out['listedDate']   = _short_date(jbzl.get('LISTED_DATE'))
            out['mainBusiness'] = jbzl.get('MAIN_BUSINESS') or jbzl.get('BUSINESS_SCOPE')
            out['profile']      = gsgk.get('ORG_PROFILE') or gsgk.get('ORG_DESCRIBE')
            out['chairman']     = jbzl.get('CHAIRMAN') or jbzl.get('LEGAL_PERSON')
            out['regCapital']   = _to_yi(jbzl.get('REG_CAPITAL'))     # 注册资本（万元 → 亿，部分接口已是元）
            out['employees']    = jbzl.get('EMP_NUM') or jbzl.get('EMPLOYEE_NUM')

            # 备选填充
            if not out.get('name'):
                out['name'] = jbzl.get('SECURITY_NAME_ABBR') or out.get('fullName')
            if not out.get('industry'):
                out['industry'] = jbzl.get('BELONG_INDUSTRY')
        except FetchError as e:
            log.warning('[f10] company survey 失败 code=%s: %s', code, e)

    # ---------------- ③ 财务主要指标：最新一期 + 近 4 期趋势 ----------------
    # 走 datacenter-web 公开 API（比 emweb F10 endpoint 稳定）
    if em_short:
        try:
            # SECUCODE 格式：'603300.SH' / '000001.SZ' / '430047.BJ'
            secucode = f'{code}.{em_short[:2]}'
            url = (
                'https://datacenter-web.eastmoney.com/api/data/v1/get'
                f'?reportName=RPT_LICO_FN_CPD'
                f'&filter=(SECUCODE%3D%22{secucode}%22)'
                f'&columns=ALL&sortColumns=REPORT_DATE&sortTypes=-1&pageSize=8'
            )
            res = fetch_json(url, headers=_f10_headers(em_short))
            zyzb = ((res or {}).get('result') or {}).get('data') or []
            if zyzb:
                latest = zyzb[0]
                out['latestPeriod'] = _short_date(_first(latest, 'REPORT_DATE'))
                # datacenter API 返回单位：营收/利润为元，转亿
                out['revenue']      = _to_yi(_first(latest, 'TOTAL_OPERATE_INCOME'))
                out['revenueYoy']   = _safe_float(_first(latest, 'YOY_TOTAL_OPERATE_INCOME', 'TOTAL_OPERATE_INCOME_YOY'))
                out['netProfit']    = _to_yi(_first(latest, 'PARENT_NETPROFIT'))
                out['netProfitYoy'] = _safe_float(_first(latest, 'YSTZ', 'PARENT_NETPROFIT_YOY'))
                out['grossMargin']  = _safe_float(_first(latest, 'XSMLL', 'GROSS_PROFIT_RATIO'))
                out['netMargin']    = _safe_float(_first(latest, 'XSJLL', 'PARENT_NETPROFIT_RATIO'))
                out['debtRatio']    = _safe_float(_first(latest, 'ZCFZL', 'DEBT_ASSET_RATIO'))
                out['eps']          = _safe_float(_first(latest, 'BASIC_EPS'))
                # 4 期营收 / 利润趋势
                out['revenueTrend'] = []
                out['netProfitTrend'] = []
                for r in zyzb[:4]:
                    out['revenueTrend'].append({
                        'period': _short_date(_first(r, 'REPORT_DATE')),
                        'value':  _to_yi(_first(r, 'TOTAL_OPERATE_INCOME')),
                        'yoy':    _safe_float(_first(r, 'YOY_TOTAL_OPERATE_INCOME', 'TOTAL_OPERATE_INCOME_YOY')),
                    })
                    out['netProfitTrend'].append({
                        'period': _short_date(_first(r, 'REPORT_DATE')),
                        'value':  _to_yi(_first(r, 'PARENT_NETPROFIT')),
                        'yoy':    _safe_float(_first(r, 'YSTZ', 'PARENT_NETPROFIT_YOY')),
                    })
        except FetchError as e:
            log.warning('[f10] finance zyzb 失败 code=%s: %s', code, e)

    # ---------------- ④ 十大流通股东 + 股东户数 ----------------
    if em_short:
        try:
            url = f'https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={em_short}'
            res = fetch_json(url, headers=_f10_headers(em_short))
            data = res or {}

            # 十大流通股东 —— 取最新报告期那一组
            sdltgd = data.get('sdltgd') or []
            if sdltgd:
                # 找最大日期
                latest_date = None
                for r in sdltgd:
                    d = _first(r, 'END_DATE', 'REPORT_DATE', 'REPORTDATE')
                    if d and (latest_date is None or str(d) > str(latest_date)):
                        latest_date = d
                top10 = []
                for r in sdltgd:
                    d = _first(r, 'END_DATE', 'REPORT_DATE', 'REPORTDATE')
                    if d != latest_date:
                        continue
                    top10.append({
                        'name':   _first(r, 'HOLDER_NAME', 'SHARES_NAME'),
                        'ratio':  _safe_float(_first(r, 'FREE_HOLDNUM_RATIO', 'HOLD_NUM_RATIO', 'HOLD_RATIO')),
                        'shares': _to_yi(_first(r, 'FREE_HOLD_NUM', 'HOLD_NUM'), 4),
                        'change': _first(r, 'HOLD_NUM_CHANGE_NAME', 'HOLD_NUM_CHANGE_NUM_STATE'),
                    })
                out['topShareholders'] = top10[:10]
                out['shareholdersDate'] = _short_date(latest_date)

            # 股东户数（最近 4 期趋势）
            gdrs = data.get('gdrs') or []
            if gdrs:
                trend = []
                for r in gdrs[:4]:
                    trend.append({
                        'period': _short_date(_first(r, 'END_DATE', 'REPORT_DATE')),
                        'count':  _safe_float(_first(r, 'HOLDER_NUM', 'HOLDER_TOTAL_NUM', 'HOLDER_NEW')),
                    })
                out['shareholderCountTrend'] = trend
                if trend:
                    out['shareholderCount'] = trend[0].get('count')
        except FetchError as e:
            log.warning('[f10] shareholders 失败 code=%s: %s', code, e)

    # ---------------- ⑤ 分红送配（最近 5 次）----------------
    if em_short:
        try:
            url = f'https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/PageAjax?code={em_short}'
            res = fetch_json(url, headers=_f10_headers(em_short))
            fhsp = (res or {}).get('fhsp') or []
            dividends = []
            for r in fhsp[:5]:
                dividends.append({
                    'year':   _short_date(_first(r, 'REPORT_DATE', 'NOTICE_DATE')),
                    'scheme': _first(r, 'IMPL_PLAN_PROFILE', 'PLAN_EXPLAIN', 'BONUS_NORMAL'),
                    'exDate': _short_date(_first(r, 'EX_DIVIDEND_DATE', 'EQUITY_RECORD_DATE')),
                })
            out['dividends'] = dividends
        except FetchError as e:
            log.warning('[f10] dividends 失败 code=%s: %s', code, e)

    # ---------------- ⑥ 概念题材 ----------------
    if em_short:
        try:
            url = f'https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax?code={em_short}'
            res = fetch_json(url, headers=_f10_headers(em_short))
            yyfw = (res or {}).get('yyfw') or []   # "经营范围" 兼容 fallback
            yyysr = (res or {}).get('yyysr') or [] # "概念题材" 主字段
            concepts = []
            for c in yyysr:
                name = c.get('NEW_BOARD_NAME') or c.get('CONCEPT_NAME') or c.get('BOARD_NAME')
                if name:
                    concepts.append(name)
            out['concepts'] = concepts[:12]  # 限 12 个，避免太长
        except FetchError as e:
            log.warning('[f10] concepts 失败 code=%s: %s', code, e)

    return out


def _first(d, *keys):
    """从 dict 按多个候选 key 取第一个非空值（容忍接口字段名差异）。"""
    for k in keys:
        v = d.get(k)
        if v is not None and v != '' and v != '-':
            return v
    return None


def _safe_float(v):
    try:
        if v is None or v == '-' or v == '':
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_yi(v, decimals=2):
    """元 / 股 → 亿 / 亿股"""
    f = _safe_float(v)
    if f is None:
        return None
    return round(f / 1e8, decimals)


def _short_date(s):
    """'2001-08-27 00:00:00' → '2001-08-27'"""
    if not s:
        return None
    s = str(s).strip()
    return s.split(' ')[0] if ' ' in s else s

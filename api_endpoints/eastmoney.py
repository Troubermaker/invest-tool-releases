"""
东方财富原始端点。
覆盖 push2.eastmoney.com / push2delay / push2his / np-weblist。
"""
import time
from http_client import fetch_json
from api_endpoints._auth import EASTMONEY_COOKIE


# ---------------- 浏览器指纹（Chrome 143 真实抓包） ---------------- #

_CHROME_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/143.0.0.0 Safari/537.36'
)

EM_BASE_HEADERS = {
    'User-Agent': _CHROME_UA,
    'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
    'sec-ch-ua-platform': '"Windows"',
    'sec-ch-ua-mobile': '?0',
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Dest': 'empty',
    'Cookie': EASTMONEY_COOKIE,
}

# 从 quote.eastmoney.com 页面发起的请求带 Referer
EM_QUOTE_HEADERS = {
    **EM_BASE_HEADERS,
    'Referer': 'https://quote.eastmoney.com/center/hszs.html',
    'Sec-Fetch-Site': 'same-site',
}

# 带 Content-Type 的接口（ulist/清单类）
EM_JSON_HEADERS = {
    **EM_BASE_HEADERS,
    'Content-Type': 'application/json;charset=UTF-8',
}


def _ts_ms():
    return str(int(time.time() * 1000))


# ---------------- 大盘指数 / 板块 ---------------- #

def raw_em_market_indices():
    """大盘指数板块列表（MK0010，约 40+ 条）"""
    url = 'https://push2.eastmoney.com/api/qt/clist/get'
    params = {
        'np': '1', 'fltt': '1', 'invt': '2',
        'fs': 'b:MK0010',
        'fields': 'f12,f13,f14,f1,f2,f4,f3,f152,f5,f6,f18,f17,f15,f16',
        'pn': '1', 'pz': '50', 'po': '1',
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        'dect': '1', 'wbp2u': '|0|0|0|web',
        '_': _ts_ms(),
    }
    return fetch_json(url, params=params, headers=EM_QUOTE_HEADERS)


def raw_em_market_indices_ulist():
    """
    指定 secid 清单查询（大全）
    默认包含 A 股主要指数 + 港股 + 美股 + 大宗，共 36 支
    """
    url = 'https://push2delay.eastmoney.com/api/qt/ulist.np/get'
    params = {
        'fltt': '2',
        'fields': 'f2,f3,f4,f6,f12,f13,f14,f20,f25,f18,f145,f100,f265,f266,f297',
        'secids': (
            '1.000001,0.399001,0.399006,1.000510,1.000985,47.800005,'
            '1.000300,1.000905,1.000852,2.932000,90.BK1158,1.000698,'
            '1.000016,0.399673,0.899050,1.000688,0.399850,100.XIN9,'
            '100.HSI,124.HSTECH,2.930604,124.HSHCI,2.931787,133.USDCNH,'
            '104.CN00Y,134.HTI_M,103.NQ00Y,118.AU9999,122.XAU,100.NDX100,'
            '100.SPX,100.N225,100.KS11,100.VNINDEX,100.SENSEX'
        ),
    }
    return fetch_json(url, params=params, headers=EM_JSON_HEADERS)


def raw_em_industry_sectors():
    """行业板块（m:90 t:2）"""
    url = 'https://push2delay.eastmoney.com/api/qt/clist/get'
    params = {
        'pn': '1', 'pz': '2000', 'po': '1', 'np': '3',
        'fid': 'f3', 'fs': 'm:90 t:2',
        'fields': ('f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,'
                   'f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,'
                   'f115,f152,f100,f103'),
        '_': _ts_ms(),
        'fltt': '2', 'invt': '2',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
    }
    return fetch_json(url, params=params, headers=EM_JSON_HEADERS)


def raw_em_concept_sectors():
    """概念板块（m:90 t:3）"""
    url = 'https://push2delay.eastmoney.com/api/qt/clist/get'
    params = {
        'pn': '1', 'pz': '2000', 'po': '1', 'np': '3',
        'fid': 'f3', 'fs': 'm:90 t:3',
        'fields': ('f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,'
                   'f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,'
                   'f115,f152,f100,f103'),
        '_': _ts_ms(),
        'fltt': '2', 'invt': '2',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
    }
    return fetch_json(url, params=params, headers=EM_JSON_HEADERS)


def raw_em_money_flow():
    """主力资金透视"""
    url = 'https://push2.eastmoney.com/api/qt/clist/get'
    params = {
        'fid': 'f3', 'po': '1', 'pz': '100', 'pn': '1', 'np': '1',
        'fltt': '2', 'invt': '2',
        'fs': ('m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,'
               'm:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2'),
        'fields': ('f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,'
                   'f84,f87,f204,f205,f124,f1,f13'),
    }
    return fetch_json(url, params=params, headers=EM_JSON_HEADERS)


def raw_em_limit_connections(secids):
    """
    涨停连板清单（按 secid 列表查）
    secids 示例: '0.001331,0.002969,1.600783,...'
    """
    url = 'https://push2delay.eastmoney.com/api/qt/ulist.np/get'
    params = {
        'fltt': '2',
        'fields': 'f12,f14,f2,f3,f4,f25,f20,f13,f18,f6,f145,f100,f265,f266,f297',
        'secids': secids,
    }
    return fetch_json(url, params=params, headers=EM_JSON_HEADERS)


# ---------------- 快讯 ---------------- #

def raw_em_fast_news():
    """快讯 7x24"""
    url = 'https://np-weblist.eastmoney.com/comm/web/getFastNewsList'
    params = {
        'client': 'web',
        'biz': 'web_724',
        'fastColumn': '102',
        'pageSize': '50',
        'req_trace': _ts_ms(),
        '_': _ts_ms(),
    }
    # 快讯接口用轻量 header（dump 里是独立 UA，不带 sec-ch-ua）
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 '
                     '(KHTML, like Gecko) Chrome/89.0.4389.114 Safari/537.36',
        'Accept-Language': 'zh-CN,en,*',
        'Cookie': EASTMONEY_COOKIE,
    }
    return fetch_json(url, params=params, headers=headers)


# ---------------- 交易日探测 ---------------- #

def raw_em_latest_kline(secid='1.000001'):
    """最新一根日 K。用于判断交易日、取最新交易日日期。"""
    url = 'http://push2his.eastmoney.com/api/qt/stock/kline/get'
    params = {
        'secid': secid,
        'fields1': 'f1',
        'fields2': 'f51',
        'klt': '101',
        'fqt': '0',
        'end': '20500000',
        'lmt': '1',
    }
    return fetch_json(url, params=params, headers=EM_BASE_HEADERS)

"""
同花顺原始端点。
覆盖 dq.10jqka.com.cn / data.10jqka.com.cn / news.10jqka.com.cn。
"""
import time
from datetime import datetime

from http_client import fetch_json
from api_endpoints._auth import TONGHUASHUN_COOKIE


# ---------------- 浏览器指纹 ---------------- #

_CHROME_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/143.0.0.0 Safari/537.36'
)

# dq.10jqka.com.cn 的 fuyao API 套（热榜、成交额、涨跌分布）
THS_FUYAO_HEADERS = {
    'User-Agent': _CHROME_UA,
    'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
    'sec-ch-ua-platform': '"Windows"',
    'sec-ch-ua-mobile': '?0',
    'Content-Type': 'application/json;charset=UTF-8',
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Storage-Access': 'active',
    'Cookie': TONGHUASHUN_COOKIE,
}

# data.10jqka.com.cn 的 datacenterph 套（涨停/连板/炸板池）
# 关键差别：需要 Referer，Sec-Fetch-Site 改 same-origin
THS_DATA_HEADERS = {
    'User-Agent': _CHROME_UA,
    'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
    'sec-ch-ua-platform': '"Windows"',
    'sec-ch-ua-mobile': '?0',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Dest': 'empty',
    'Referer': 'https://data.10jqka.com.cn/datacenterph/limitup/limtupInfo.html',
    'Cookie': TONGHUASHUN_COOKIE,
}

# news.10jqka 快讯用轻量 header
THS_NEWS_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 '
                 '(KHTML, like Gecko) Chrome/89.0.4389.114 Safari/537.36',
    'Accept-Language': 'zh-CN,en,*',
}


def _ts_ms():
    return str(int(time.time() * 1000))


# ---------------- 市场情绪 ---------------- #

def raw_ths_up_down_distribution():
    """涨跌分布（实时）"""
    url = 'https://dq.10jqka.com.cn/fuyao/up_down_distribution/distribution/v2/realtime'
    return fetch_json(url, headers=THS_FUYAO_HEADERS)


def raw_ths_turnover_minute():
    """市场成交额分时"""
    url = 'https://dq.10jqka.com.cn/fuyao/market_analysis_api/chart/v1/get_chart_data'
    params = {'chart_key': 'turnover_minute'}
    return fetch_json(url, params=params, headers=THS_FUYAO_HEADERS)


# ---------------- 热榜 ---------------- #

def raw_ths_hot_list_day():
    """热榜 24 小时"""
    url = 'https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock'
    params = {'stock_type': 'a', 'type': 'day', 'list_type': 'normal'}
    return fetch_json(url, params=params, headers=THS_FUYAO_HEADERS)


def raw_ths_hot_list_hour():
    """热榜 1 小时"""
    url = 'https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock'
    params = {'stock_type': 'a', 'type': 'hour', 'list_type': 'normal'}
    return fetch_json(url, params=params, headers=THS_FUYAO_HEADERS)


# ---------------- 涨停 / 连板 / 炸板 / 跌停 ---------------- #

def raw_ths_limit_up_pool(date=''):
    """涨停池。date 格式 YYYYMMDD，空取最新。"""
    url = 'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool'
    params = {
        'page': '1', 'limit': '100',
        'field': ('199112,10,9001,330323,330324,330325,9002,'
                  '330329,133971,133970,1968584,3475914,9003,9004'),
        'filter': 'HS,GEM2STAR',
        'order_field': '330324', 'order_type': '0',
        'date': date,
        '_': _ts_ms(),
    }
    return fetch_json(url, params=params, headers=THS_DATA_HEADERS)


def raw_ths_continuous_limit_pool(date=''):
    """连板池"""
    url = 'https://data.10jqka.com.cn/dataapi/limit_up/continuous_limit_pool'
    params = {
        'page': '1', 'limit': '100',
        'field': '199112,10,330329,330325,133971,133970,1968584,3475914,3541450,9004',
        'filter': 'HS,GEM2STAR',
        'order_field': '330329', 'order_type': '0',
        'date': date,
        '_': _ts_ms(),
    }
    return fetch_json(url, params=params, headers=THS_DATA_HEADERS)


def raw_ths_open_limit_pool(date=''):
    """炸板池"""
    url = 'https://data.10jqka.com.cn/dataapi/limit_up/open_limit_pool'
    params = {
        'page': '1', 'limit': '100',
        'field': '199112,9002,48,1968584,19,3475914,9003,10,9004',
        'filter': 'HS,GEM2STAR',
        'order_field': '199112', 'order_type': '0',
        'date': date,
        '_': _ts_ms(),
    }
    return fetch_json(url, params=params, headers=THS_DATA_HEADERS)


def raw_ths_lower_limit_pool(date=''):
    """跌停池（新增）"""
    url = 'https://data.10jqka.com.cn/dataapi/limit_up/lower_limit_pool'
    params = {
        'page': '1', 'limit': '100',
        'field': '199112,10,330333,330334,1968584,3475914,9004',
        'filter': 'HS,GEM2STAR',
        'order_field': '330334', 'order_type': '0',
        'date': date,
        '_': _ts_ms(),
    }
    return fetch_json(url, params=params, headers=THS_DATA_HEADERS)


def raw_ths_limit_up_sprint(date=''):
    """冲刺涨停（新增）"""
    url = 'https://data.10jqka.com.cn/dataapi/limit_up/limit_up'
    params = {
        'page': '1', 'limit': '100',
        'field': '199112,10,48,1968584,19,3475914,9003,9004',
        'filter': 'HS,GEM2STAR',
        'order_field': '199112', 'order_type': '0',
        'date': date,
        '_': _ts_ms(),
    }
    return fetch_json(url, params=params, headers=THS_DATA_HEADERS)


def raw_ths_continuous_limit_up(date=''):
    """连板天梯（新增）"""
    url = 'https://data.10jqka.com.cn/dataapi/limit_up/continuous_limit_up'
    params = {
        'filter': 'HS,GEM2STAR',
        'date': date or datetime.now().strftime('%Y%m%d'),
    }
    return fetch_json(url, params=params, headers=THS_DATA_HEADERS)


def raw_ths_block_top(date=''):
    """最强风口（板块强度榜）"""
    url = 'https://data.10jqka.com.cn/dataapi/limit_up/block_top'
    params = {
        'filter': 'HS,GEM2STAR',
        'date': date or datetime.now().strftime('%Y%m%d'),
    }
    return fetch_json(url, params=params, headers=THS_DATA_HEADERS)


# ---------------- 快讯 ---------------- #

def raw_ths_fast_news():
    """同花顺快讯"""
    url = 'https://news.10jqka.com.cn/pclient/news/push/stock/1.json'
    return fetch_json(url, headers=THS_NEWS_HEADERS)

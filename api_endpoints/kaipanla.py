"""
开盘啦原始端点。
UA 必须伪装成 Android 客户端，且大多数接口需要 UserID + Token 登录态。
"""
from datetime import datetime, time as dtime

from http_client import fetch_json
from api_endpoints._auth import KPL_USER_ID, KPL_TOKEN


def _compute_rend():
    """
    根据当前时间生成 KPL 接口的 REnd 参数（HHMM 格式）。
    - 盘中（9:30-15:00）：当前时间向下取整到 10 分钟（如 13:47 → '1340'）
    - 盘前/盘后/周末：返回 '1500'（表示"取收盘快照"）
    午休时段也走"盘中"分支，KPL 服务端会用 11:30 前最后一根 bar 响应。
    """
    now = datetime.now()
    if now.weekday() >= 5:  # 周六周日
        return '1500'
    cur = now.time()
    if cur >= dtime(15, 0) or cur < dtime(9, 30):
        return '1500'
    minute_rounded = (now.minute // 10) * 10
    return f'{now.hour:02d}{minute_rounded:02d}'


ANDROID_UA = 'Dalvik/2.1.0 (Linux; U; Android 5.1.1; ASUS_I005DA Build/LMY48Z)'

BASE_HEADERS = {
    'User-Agent': ANDROID_UA,
    'Accept-Encoding': 'gzip, deflate',
    'Accept-Language': 'zh-CN,en,*',
    'Connection': 'Keep-Alive',
    'Content-Type': 'application/json',
}


def _auth_params():
    """所有需要登录态的接口都 merge 这份基础参数"""
    return {'UserID': KPL_USER_ID, 'Token': KPL_TOKEN}


# ---------------- 精选板块 ---------------- #

def raw_kpl_real_ranking():
    """精选板块实时榜（ZSType=7）。当前交易日实时数据，走 apphq。"""
    url = 'https://apphq.longhuvip.com/w1/api/index.php'
    params = {
        'Order': '1',
        'a': 'RealRankingInfo',
        'st': '60', 'Type': '1',
        'c': 'ZhiShuRanking',
        'PhoneOSNew': '1', 'Index': '0',
        'ZSType': '7',
    }
    return fetch_json(url, params=params, headers={**BASE_HEADERS, 'Host': 'apphq.longhuvip.com'})


def raw_kpl_real_ranking_historical(date):
    """
    精选板块榜 - 历史日期版（apphis 域名）。
    Args:
        date: 'YYYY-MM-DD' 格式（KPL 历史接口要求带短横线）
    """
    url = 'https://apphis.longhuvip.com/w1/api/index.php'
    params = {
        'Order': '1',
        'a': 'RealRankingInfo',
        'st': '60',
        'c': 'ZhiShuRanking',
        'PhoneOSNew': '1',
        'Index': '0',
        'Date': date,
        'Type': '1',
        'ZSType': '7',
    }
    return fetch_json(url, params=params, headers={**BASE_HEADERS, 'Host': 'apphis.longhuvip.com'})


def raw_kpl_plate_stocks(plate_id='801001'):
    """
    精选板块联动个股（ZhiShuStockList_W8）—— 当前交易日实时，走 apphq。
    注：2026-04 重抓后发现接口迁到 apphq.longhuvip.com 并强制要求 UserID
    参数顺序严格按原 app 抓包复刻（KPL 的 PHP 后端疑似对顺序敏感）
    """
    url = 'https://apphq.longhuvip.com/w1/api/index.php'
    params = {
        'Order': '1',
        'a': 'ZhiShuStockList_W8',
        'st': '60',
        'c': 'ZhiShuRanking',
        'PhoneOSNew': '1',
        'RStart': '0925',
        'old': '1',
        'IsZZ': '0',
        'Token': '0',
        'Index': '0',      # 分页偏移，0=第一页（前 60 只热门股，含龙头/连板数据）
        'REnd': _compute_rend(),  # 动态：盘中取整 10 分钟 / 盘后用 1500
        'Type': '6',
        'IsKZZType': '0',
        'PlateID': plate_id,
        'Isst': '1',
        'FilterMotherboard': '0',
        'Filter': '0',
        'Ratio': '6',
        'FilterTIB': '0',
        'FilterGem': '0',
        'UserID': KPL_USER_ID,
    }
    return fetch_json(url, params=params, headers={**BASE_HEADERS, 'Host': 'apphq.longhuvip.com'})


def raw_kpl_plate_stocks_historical(plate_id, date):
    """
    精选板块联动股 - 历史日期版（apphis 域名）。
    历史接口比实时少了一堆 Filter/Ratio/UserID 参数，多了 TSZB 系列；
    每页 30 只（实时是 60）。
    Args:
        plate_id: 板块 id（如 '801001'）
        date:     'YYYY-MM-DD' 格式
    """
    url = 'https://apphis.longhuvip.com/w1/api/index.php'
    params = {
        'Order': '1',
        'TSZB': '0',
        'a': 'ZhiShuStockList_W8',
        'st': '30',
        'c': 'ZhiShuRanking',
        'PhoneOSNew': '1',
        'old': '1',
        'IsZZ': '0',
        'Index': '0',
        'Date': date,
        'Type': '6',
        'IsKZZType': '0',
        'PlateID': plate_id,
        'TSZB_Type': '0',
        'filterType': '0',
    }
    return fetch_json(url, params=params, headers={**BASE_HEADERS, 'Host': 'apphis.longhuvip.com'})


# ---------------- 竞价 / 异动 ---------------- #

def raw_kpl_morning_bidding():
    """早盘竞价（需登录）"""
    url = 'https://apphwhq.longhuvip.com/w1/api/index.php'
    params = {
        'Order': '1',
        'a': 'MorningBiddingList',
        'st': '60', 'c': 'HomeDingPan',
        'PhoneOSNew': '1',
        'Index': '0', 'PidType': '0', 'Type': '4',
        **_auth_params(),
    }
    return fetch_json(url, params=params, headers={**BASE_HEADERS, 'Host': 'apphwhq.longhuvip.com'})


def raw_kpl_block_bid_change():
    """板块竞价异动（GetBKJJ_w36，需登录）"""
    url = 'https://apphq.longhuvip.com/w1/api/index.php'
    params = {
        'Order': '1', 'st': '60',
        'a': 'GetBKJJ_w36',
        'c': 'StockBidYiDong',
        'PhoneOSNew': '1',
        'Index': '0', 'Type': '1',
        **_auth_params(),
    }
    return fetch_json(url, params=params, headers={**BASE_HEADERS, 'Host': 'apphq.longhuvip.com'})


def raw_kpl_block_bid_stocks(stock_id='801001'):
    """板块竞价联动个股（GetBKJJBL，需登录）"""
    url = 'https://apphwhq.longhuvip.com/w1/api/index.php'
    params = {
        'Order': '1',
        'a': 'GetBKJJBL',
        'st': '60', 'IsLB': '0',
        'c': 'StockBidYiDong',
        'PhoneOSNew': '1',
        'IsZT': '0', 'Isst': '1',
        'Index': '0', 'filter': '1',
        'apiv': 'w36', 'Type': '3',
        'StockID': stock_id,
        **_auth_params(),
    }
    return fetch_json(url, params=params, headers={**BASE_HEADERS, 'Host': 'apphwhq.longhuvip.com'})


def raw_kpl_tail_rush():
    """尾盘抢筹（GetWPQC，需登录）"""
    url = 'https://apphwhq.longhuvip.com/w1/api/index.php'
    params = {
        'Order': '1',
        'a': 'GetWPQC',
        'st': '1000',
        'c': 'StockBidYiDong',
        'Index': '0', 'Type': '1',
        **_auth_params(),
    }
    return fetch_json(url, params=params, headers={**BASE_HEADERS, 'Host': 'apphwhq.longhuvip.com'})


# ---------------- 风口 / 轮动 / 话题 ---------------- #

def raw_kpl_best_fengkou():
    """最强风口板块"""
    url = 'https://apphwshhq.longhuvip.com/w1/api/index.php'
    params = {
        'c': 'StockFengKData',
        'a': 'GetFengKListBest',
    }
    return fetch_json(url, params=params, headers={**BASE_HEADERS, 'Host': 'apphwshhq.longhuvip.com'})


def raw_kpl_plate_rotation():
    """板块轮动（涨停复盘）"""
    url = 'https://apphq.longhuvip.com/w1/api/index.php'
    params = {
        'a': 'GetPlateInfo',
        'st': '1000',
        'c': 'DailyLimitResumption',
        'Index': '0',
    }
    return fetch_json(url, params=params, headers={**BASE_HEADERS, 'Host': 'apphq.longhuvip.com'})


def raw_kpl_topic():
    """明天炒什么（话题列表，需登录）"""
    url = 'https://applhb.longhuvip.com/w1/api/index.php'
    params = {
        'a': 'InfoList',
        'st': '1000',
        'c': 'Topic',
        'PhoneOSNew': '1',
        'index': '0',
        **_auth_params(),
    }
    return fetch_json(url, params=params, headers={**BASE_HEADERS, 'Host': 'applhb.longhuvip.com'})

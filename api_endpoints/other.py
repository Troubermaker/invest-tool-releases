"""
小数据源原始端点：选股宝 / 大智慧 / 短线侠。
"""
from datetime import datetime

from http_client import fetch_json, post_json
from api_endpoints._auth import DZH_COOKIE


_CHROME_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/143.0.0.0 Safari/537.36'
)


# ---------------- 选股宝 ---------------- #

def raw_xgb_limit_up_broken():
    """选股宝涨停炸板池"""
    url = 'https://flash-api.xuangubao.com.cn/api/pool/detail'
    params = {'pool_name': 'limit_up_broken'}
    headers = {
        'User-Agent': 'Dalvik/2.1.0 (Linux; U; Android 5.1.1; ASUS_I005DA Build/LMY48Z)',
        'Accept-Encoding': 'gzip, deflate',
        'Accept-Language': 'zh-CN,en,*',
    }
    return fetch_json(url, params=params, headers=headers)


# ---------------- 大智慧 ---------------- #

def raw_dzh_zttt(date=''):
    """大智慧涨停天梯。date 格式 YYYYMMDD，空取今天。"""
    url = 'https://webrelease.dzh.com.cn/htmlweb/ztts/api.php'
    params = {
        'service': 'getZttdData',
        'date': date or datetime.now().strftime('%Y%m%d'),
    }
    headers = {
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
        'Cookie': DZH_COOKIE,
    }
    return fetch_json(url, params=params, headers=headers)


# ---------------- 短线侠（POST 接口）---------------- #

def raw_dxx_bid_fengdan():
    """短线侠竞价封单（POST，无请求体）"""
    url = 'https://duanxianxia.com/api/getFengdanLast'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 '
                     '(KHTML, like Gecko) Chrome/89.0.4389.114 Safari/537.36',
        'Accept-Encoding': 'gzip, deflate',
        'Accept-Language': 'zh-CN,en,*',
        'Connection': 'Keep-Alive',
        'Referer': 'https://duanxianxia.com/web/jjlive',
    }
    return post_json(url, headers=headers)

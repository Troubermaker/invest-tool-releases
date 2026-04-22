"""
统一 HTTP 客户端。

特性：
- requests.Session 全局单例：连接池复用，多次调用同一域名显著加速
- 分域名 RateLimiter：不同数据源不同节流（THS 严格慢、EM 宽松快）
- 调用间隔 jitter：在目标间隔上随机 ±30%，消除完美周期这个 bot 特征
- 指数退避 retry：默认最多重试 2 次
- 统一 FetchError：上层 service 只处理一种异常类型
- 所有 SSL 校验默认关闭（适配部分第三方 API 的证书问题）
- 支持 GET / POST

公开接口：
    fetch_text(url, *, params, encoding, headers, ...)   → 解码后正文字符串
    fetch_json(url, *, params, headers, ...)             → JSON 解析结果
    post_json(url, *, json_body, data, headers, ...)     → POST 请求
    set_rate_limit(domain, calls_per_second)             → 运行时调整某域名节流
"""
import json
import logging
import random
import threading
import time
from urllib.parse import urlparse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
DEFAULT_RETRY = 2

# 分域名节流配置 (calls per second)
# THS/data.10jqka 反爬严格 → 慢；EM 相对宽松 → 快；其它中等
DOMAIN_RATE_LIMITS = {
    # 同花顺
    'dq.10jqka.com.cn': 0.8,
    'data.10jqka.com.cn': 0.8,
    'news.10jqka.com.cn': 1.0,
    # 开盘啦
    'apphq.longhuvip.com': 1.2,
    'apphwhq.longhuvip.com': 1.2,
    'apphwshhq.longhuvip.com': 1.2,
    'applhb.longhuvip.com': 1.2,
    # 东方财富（相对宽松）
    'push2.eastmoney.com': 3.0,
    'push2delay.eastmoney.com': 3.0,
    'push2his.eastmoney.com': 3.0,
    'np-weblist.eastmoney.com': 2.0,
    # 其它
    'flash-api.xuangubao.com.cn': 2.0,
    'webrelease.dzh.com.cn': 2.0,
    'duanxianxia.com': 1.5,
    # 新浪 / 腾讯（老数据源）
    'hq.sinajs.cn': 3.0,
    'vip.stock.finance.sina.com.cn': 2.0,
    'money.finance.sina.com.cn': 2.0,
    'ifzq.gtimg.cn': 3.0,
    'proxy.finance.qq.com': 2.0,
}
DEFAULT_DOMAIN_RATE = 2.0

# 节拍 jitter：实际间隔在 [min_interval * 0.7, min_interval * 1.3] 间随机
JITTER_RATIO = 0.3

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36"
    )
}


class FetchError(Exception):
    """统一错误类型，带 code 字段。"""
    def __init__(self, message, code="FETCH_ERROR"):
        super().__init__(message)
        self.code = code


class _RateLimiter:
    """线程安全的单域名节流器，带 jitter。"""
    def __init__(self, calls_per_second):
        self.base_interval = 1.0 / calls_per_second
        self.last_call = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delta = now - self.last_call
            # jitter：实际间隔 = base ± 30% 随机
            jitter = random.uniform(-JITTER_RATIO, JITTER_RATIO)
            required = self.base_interval * (1 + jitter)
            if delta < required:
                time.sleep(required - delta)
            self.last_call = time.monotonic()


# 全局 Session + 每域名 RateLimiter（lazy init）
_session = requests.Session()
_domain_limiters = {}
_domain_limiters_lock = threading.Lock()


def _get_limiter(url):
    """根据 URL 域名获取对应节流器，未配置的域名走默认值。"""
    host = urlparse(url).hostname or ''
    rate = DOMAIN_RATE_LIMITS.get(host, DEFAULT_DOMAIN_RATE)
    with _domain_limiters_lock:
        if host not in _domain_limiters:
            _domain_limiters[host] = _RateLimiter(rate)
        return _domain_limiters[host]


def set_rate_limit(domain, calls_per_second):
    """运行时调整某域名的节流率。"""
    with _domain_limiters_lock:
        _domain_limiters[domain] = _RateLimiter(calls_per_second)


def _do_request(method, url, *, params=None, data=None, json_body=None,
                encoding="utf-8", headers=None, timeout=DEFAULT_TIMEOUT,
                retry=DEFAULT_RETRY, verify=False):
    """内部统一请求函数，处理 retry / rate limit / 错误分类。"""
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}
    last_error = ("UNKNOWN", "no attempt made")

    for attempt in range(retry + 1):
        _get_limiter(url).wait()
        try:
            if method == 'GET':
                res = _session.get(
                    url, params=params, headers=merged_headers,
                    timeout=timeout, verify=verify,
                )
            elif method == 'POST':
                res = _session.post(
                    url, params=params, data=data, json=json_body,
                    headers=merged_headers, timeout=timeout, verify=verify,
                )
            else:
                raise ValueError(f"不支持的 HTTP 方法: {method}")

            res.encoding = encoding
            res.raise_for_status()
            return res.text
        except requests.Timeout as e:
            last_error = ("TIMEOUT", str(e))
        except requests.ConnectionError as e:
            last_error = ("CONNECTION", str(e))
        except requests.HTTPError as e:
            # 403/429 可能是被风控，多等一会
            code = e.response.status_code if e.response else 0
            if code in (403, 429):
                last_error = ("RATE_LIMITED", f"{code} {e}")
                logger.warning(f"{url} 疑似触发风控 ({code})，延长退避")
                if attempt < retry:
                    time.sleep(5.0 * (attempt + 1))  # 风控冷却更长
                    continue
            else:
                last_error = ("HTTP", f"{code} {e}")
        except Exception as e:
            last_error = ("UNKNOWN", str(e))

        if attempt < retry:
            wait_s = 1.0 * (attempt + 1)
            logger.warning(
                f"{url} 第 {attempt + 1} 次失败 ({last_error[0]}: {last_error[1]}), "
                f"{wait_s}s 后重试"
            )
            time.sleep(wait_s)

    code, msg = last_error
    logger.error(f"{url} 最终失败: {msg}")
    raise FetchError(f"{url} 请求失败: {msg}", code=code)


def fetch_text(url, *, params=None, encoding="utf-8", headers=None,
               timeout=DEFAULT_TIMEOUT, retry=DEFAULT_RETRY, verify=False):
    """GET 请求，返回解码正文字符串。"""
    return _do_request('GET', url, params=params, encoding=encoding,
                       headers=headers, timeout=timeout, retry=retry, verify=verify)


def fetch_json(url, *, params=None, headers=None,
               timeout=DEFAULT_TIMEOUT, retry=DEFAULT_RETRY, verify=False):
    """GET 请求并 JSON 解析。"""
    text = _do_request('GET', url, params=params, encoding="utf-8",
                       headers=headers, timeout=timeout, retry=retry, verify=verify)
    try:
        return json.loads(text)
    except Exception as e:
        raise FetchError(f"{url} JSON 解析失败: {e}", code="PARSE_ERROR")


def post_json(url, *, json_body=None, data=None, params=None, headers=None,
              timeout=DEFAULT_TIMEOUT, retry=DEFAULT_RETRY, verify=False):
    """POST 请求并 JSON 解析。json_body 走 JSON 体，data 走 form。"""
    text = _do_request('POST', url, params=params, data=data, json_body=json_body,
                       encoding="utf-8", headers=headers,
                       timeout=timeout, retry=retry, verify=verify)
    try:
        return json.loads(text)
    except Exception as e:
        raise FetchError(f"{url} JSON 解析失败: {e}", code="PARSE_ERROR")

"""
统一 HTTP 客户端。

特性：
- curl_cffi 底层：impersonate Chrome 的 TLS 指纹（JA3）+ HTTP/2，绕过基于
  TLS 指纹检测的反爬（东方财富 push2his 等专业反爬系统会按 JA3 hash 识别脚本）
- Session 全局单例：连接池复用，多次调用同一域名显著加速
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

# 优先使用 curl_cffi（impersonate Chrome 的 TLS 握手，反爬难识别）；
# 如果环境里没装，fallback 到普通 requests，功能还在但会被基于 JA3 的反爬抓出来
# 异常类型必须显式从 .exceptions 子模块取 — curl_cffi 不像 requests 那样在顶层 alias
try:
    from curl_cffi import requests as cffi_requests
    from curl_cffi.requests.exceptions import (
        Timeout as _CFFITimeout,
        ConnectionError as _CFFIConnectionError,
        HTTPError as _CFFIHTTPError,
    )
    _HAS_CURL_CFFI = True
except Exception:
    import requests as cffi_requests
    from requests.exceptions import (
        Timeout as _CFFITimeout,
        ConnectionError as _CFFIConnectionError,
        HTTPError as _CFFIHTTPError,
    )
    _HAS_CURL_CFFI = False

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# curl_cffi impersonate 目标：用最新 Chrome（指纹最贴近多数真实用户）
# 如果某天 EM 改了反爬规则，可以改 'chrome124' / 'safari17_2' 等其它目标试
IMPERSONATE_TARGET = 'chrome131' if _HAS_CURL_CFFI else None

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15  # 部分东财接口盘中峰值响应较慢，10s 容易触发超时；放宽到 15s
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
    'push2delay.eastmoney.com': 3.0,  # 安全优先：保持 3/s 避免被反爬识别
    'push2his.eastmoney.com': 2.0,    # 历史 K 线接口在批量扫描时易被 RST，降到 2/s 留余地
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

# 节拍 jitter：实际间隔在 [min_interval * 0.5, min_interval * 1.5] 间随机
# 0.5 比 0.3 更不规则，更难被反爬识别为定时任务
JITTER_RATIO = 0.5

# UA 池：真实主流浏览器 + Win/Mac 混合，每次请求随机选一个，避免"同一 UA 高频"
# 这种容易被识别为机器人的 fingerprint。覆盖 Chrome 多版本 / Edge / Safari / Firefox
USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
    # Chrome on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    # Safari on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
]


def _pick_browser_headers():
    """每次请求随机选一组真实浏览器 headers（UA + 配套 Accept/Sec-Fetch 等）。"""
    ua = random.choice(USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    # 现代 Chromium 浏览器都会发 Sec-Fetch-* 头，只对 Chrome/Edge UA 加上才不穿帮
    if 'Chrome/' in ua:
        headers.update({
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        })
    return headers


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


def _new_session():
    """创建一个新的 Session（curl_cffi 的或 requests 的，看哪个可用）。"""
    return cffi_requests.Session()


# 全局 Session + 每域名 RateLimiter（lazy init）
_session = _new_session()
_session_lock = threading.Lock()
_domain_limiters = {}
_domain_limiters_lock = threading.Lock()


def _reset_session():
    """关掉当前 session 的连接池并换一个新的。

    用于 ConnectionError（典型如 RemoteDisconnected）后强制丢弃所有 keep-alive
    旧连接 — EM 等数据源的 LB 会限制单连接请求次数，复用过度会被主动 RST。
    重建 session 后下次请求会握手全新 TCP，多数情况下重试就能成功。
    """
    global _session
    with _session_lock:
        try:
            _session.close()
        except Exception:
            pass
        _session = _new_session()


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
    last_error = ("UNKNOWN", "no attempt made")

    # curl_cffi 用 impersonate kwarg 让 TLS 握手伪装成真实浏览器
    # requests 没这个参数，需要按可用性条件传
    extra_kwargs = {'impersonate': IMPERSONATE_TARGET} if _HAS_CURL_CFFI else {}

    for attempt in range(retry + 1):
        _get_limiter(url).wait()
        # 每次尝试都重新摇一组 headers — 同一个 URL 重试时换 UA
        # 增加"两次失败但 UA 不同 → 像不同用户"的迷惑度
        merged_headers = {**_pick_browser_headers(), **(headers or {})}
        try:
            if method == 'GET':
                res = _session.get(
                    url, params=params, headers=merged_headers,
                    timeout=timeout, verify=verify,
                    **extra_kwargs,
                )
            elif method == 'POST':
                res = _session.post(
                    url, params=params, data=data, json=json_body,
                    headers=merged_headers, timeout=timeout, verify=verify,
                    **extra_kwargs,
                )
            else:
                raise ValueError(f"不支持的 HTTP 方法: {method}")

            res.encoding = encoding
            res.raise_for_status()
            return res.text
        except _CFFITimeout as e:
            last_error = ("TIMEOUT", str(e))
        except _CFFIConnectionError as e:
            # 典型场景：keep-alive 连接被对端 RST（RemoteDisconnected）
            # 或者 curl: (56) Connection closed abruptly（EM 主动切 TLS）
            # 立即重建 session，下次重试会握手新 TCP；如果是 EM 这类反爬严格的
            # 数据源，配合上层断路器（kline_service）会快速跳过
            last_error = ("CONNECTION", str(e))
            _reset_session()
        except _CFFIHTTPError as e:
            # 403/429 可能是被风控，多等一会
            response = getattr(e, 'response', None)
            code = response.status_code if response is not None else 0
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
            # TIMEOUT/CONNECTION 是会自愈的瞬时错误，降到 DEBUG 不污染日志；
            # HTTP/UNKNOWN 是潜在问题，保留 WARNING
            log_fn = logger.debug if last_error[0] in ('TIMEOUT', 'CONNECTION') else logger.warning
            log_fn(
                f"{url} 第 {attempt + 1} 次失败 ({last_error[0]}: {last_error[1]}), "
                f"{wait_s}s 后重试"
            )
            time.sleep(wait_s)

    code, msg = last_error
    # CONNECTION/TIMEOUT 通常会被上层 service fallback 救活（如 EM → 腾讯），
    # 降级到 warning 避免日志看起来像功能挂了；HTTP/UNKNOWN 才是真问题，保留 error
    log_fn = logger.warning if code in ('CONNECTION', 'TIMEOUT') else logger.error
    log_fn(f"{url} 最终失败: {msg}")
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
        # 部分服务器（如东财部分 F10 端点）会在 JSON 前加 UTF-8 BOM (﻿)，先剥掉
        return json.loads(text.lstrip('﻿'))
    except Exception as e:
        raise FetchError(f"{url} JSON 解析失败: {e}", code="PARSE_ERROR")


def post_json(url, *, json_body=None, data=None, params=None, headers=None,
              timeout=DEFAULT_TIMEOUT, retry=DEFAULT_RETRY, verify=False):
    """POST 请求并 JSON 解析。json_body 走 JSON 体，data 走 form。"""
    text = _do_request('POST', url, params=params, data=data, json_body=json_body,
                       encoding="utf-8", headers=headers,
                       timeout=timeout, retry=retry, verify=verify)
    try:
        return json.loads(text.lstrip('﻿'))
    except Exception as e:
        raise FetchError(f"{url} JSON 解析失败: {e}", code="PARSE_ERROR")

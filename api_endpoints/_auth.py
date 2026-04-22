"""
第三方 API 的认证信息集中管理。

⚠️  Cookie / Token 会过期（通常几周到几个月）。
    过期后接口会返 401 / 403 / 空数据 / 错误码，需要重新从浏览器 DevTools 抓取新值更新本文件。

更新步骤：
    1. 打开对应数据源网站（EM / THS / DZH 等）
    2. KPL 的需要登录一次，打开 App 抓包或 mitmproxy 抓 app 请求
    3. F12 → Network → XHR → 刷新页面
    4. 找到任一目标接口的请求头 → 复制整串 Cookie → 粘贴到下方对应变量
    5. 保存后重启后端即可（Session 会自动用新 Cookie）

注意：本文件**不要**提交到公开代码仓库（含登录态）。
"""

# ============================================
# 东方财富（EM）
# ============================================
# 覆盖 push2.eastmoney.com / push2delay / push2his / np-weblist
# 这些 cookie 来自 quote.eastmoney.com 首页，无需登录也能用
EASTMONEY_COOKIE = (
    "qgqp_b_id=69ccc1be9ea3515f8f3bc5bf1db142ff; "
    "st_nvi=k9dlg__eIZNyY_3nFePUg33b9; "
    "nid18=01af8ba102649e06f78e4384ed2b28d8; "
    "nid18_create_time=1766667702224; "
    "gviem=AqJMTsVFjgfRzT8LUJEt_e07a; "
    "gviem_create_time=1766667702224; "
    "st_pvi=31421978404947; "
    "st_sp=2025-08-05%2016%3A57%3A46; "
    "st_inirUrl=https%3A%2F%2Fquote.eastmoney.com%2F"
)

# ============================================
# 同花顺（THS）
# ============================================
# 覆盖 dq.10jqka.com.cn / data.10jqka.com.cn / news.10jqka.com.cn
# 'v=' 字段是 THS 反爬的核心 cookie，过期最快（约 7-30 天）
# 如果接口返 {"status_code": -1} 或 403，优先检查此 cookie
TONGHUASHUN_COOKIE = (
    "Hm_lvt_722143063e4892925903024537075d0d=1756369478; "
    "Hm_lvt_929f8b362150b1f77b477230541dbbc2=1756369478; "
    "hxmPid=free_ztjj; "
    "Hm_lvt_69929b9dce4c22a060bd22d703b2a280=1764860751; "
    "_ga=GA1.1.626057668.1764860752; "
    "Hm_lvt_78c58f01938e4d85eaf619eae71b4ed1=1764860771; "
    "Hm_lvt_60bad21af9c824a4a0530d5dbf4357ca=1764860771; "
    "Hm_lvt_f79b64788a4e377c608617fba4c736e2=1764860771; "
    "__utma=156575163.626057668.1764860752.1764860810.1764860810.1; "
    "__utmz=156575163.1764860810.1.1.utmcsr=(direct)|utmccn=(direct)|utmcmd=(none); "
    "_clck=capisi%7C2%7Cg1k%7C0%7C0; "
    "_ga_H2RK0R0681=GS2.1.s1764860752$o1$g1$t1764860889$j60$l0$h0; "
    "v=A72TAThTiJFbySz92jDsEO1jzBKyWvCc-45VgH8C-ZRDttNMR6oBfIveZUYM"
)

# ============================================
# 大智慧（DZH）
# ============================================
DZH_COOKIE = (
    "sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%221989d7723be1e3e-0e8fc471374606-26011151-2073600-1989d7723bf1b5b%22%7D"
)

# ============================================
# 开盘啦（KPL）
# ============================================
# KPL 需要登录态：UserID + Token，作为 URL 参数传（不是 cookie）
# Token 有效期较短，失效时 API 返 -1 或空数据
# Dump 里有两套，大多数接口用 A，topic 接口历史上用 B —— 保留两套便于排查
KPL_USER_ID = '4466914'
KPL_TOKEN = '95c0628ea943f941840f22e843feb343'

# 备用账号（抓 topic 时用的那套，如有需要）
KPL_USER_ID_B = '2653861'
KPL_TOKEN_B = 'f4ae7604feffe0fc70be9fd50632a128'

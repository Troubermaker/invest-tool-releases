"""
集中管理所有市场 symbol / code 映射。

避免在多处（新浪行情、东财 K 线、腾讯分时）重复维护同一份代码表。
新增指数/股票只需要在这里加一行，所有 service 自动受益。
"""

# 核心指数清单
# 每行: (显示名, 新浪行情代码, 通用短码, 东财市场前缀)
#
# - 新浪行情代码：hq.sinajs.cn/list 接口用，如 s_sh000001
# - 通用短码：新浪 K 线 / 腾讯分时接口用，如 sh000001
# - 东财市场前缀：1=沪市(sh), 0=深市+北交所(sz/bj)
CORE_INDICES = [
    ('上证指数',  's_sh000001', 'sh000001', '1'),
    ('深证成指',  's_sz399001', 'sz399001', '0'),
    ('创业板指',  's_sz399006', 'sz399006', '0'),
    ('科创50',    's_sh000688', 'sh000688', '1'),
    ('北证50',    's_bj899050', 'bj899050', '0'),
    ('沪深300',   's_sh000300', 'sh000300', '1'),
    ('中证500',   's_sh000905', 'sh000905', '1'),
    ('中证1000',  's_sh000852', 'sh000852', '1'),
]

# 计入"全市场成交额"概览的索引（仅沪深主板）
TOTAL_TURNOVER_CONTRIBUTORS = {'s_sh000001', 's_sz399001'}


def get_sina_quote_codes():
    """返回 ['s_sh000001', 's_sz399001', ...] 用于新浪 list 接口"""
    return [s for _, s, _, _ in CORE_INDICES]


def get_names_with_sina_codes():
    """返回 [(显示名, 新浪代码), ...]"""
    return [(n, s) for n, s, _, _ in CORE_INDICES]


def get_short_code(name):
    """按显示名查通用短码，如 '上证指数' -> 'sh000001'"""
    for n, _, c, _ in CORE_INDICES:
        if n == name:
            return c
    return None


def get_eastmoney_secid(name):
    """按显示名拼 EastMoney secid，如 '上证指数' -> '1.000001'"""
    for n, _, c, prefix in CORE_INDICES:
        if n == name:
            return f"{prefix}.{c[2:]}"
    return None

"""
向后兼容转发层。

新代码不应该 import fetcher，应该直接 import services.xxx_service。
本文件保留仅为兼容现有 scheduler.py / test_*.py 等老调用点。
后续清理可以一次性删除本文件，把上述老调用点改成 services.xxx_service 即可。
"""
from services.market_service import get_market_indices  # noqa: F401
from services.sector_service import get_hot_sectors      # noqa: F401
from services.kline_service import get_kline as _get_kline


def get_kline_data(index_name, timeframe):
    """旧名字转发到新服务。"""
    return _get_kline(index_name, timeframe)


# 旧工具函数：行情专用的时效判断，现已迁移到 db.py
from db import is_market_cache_stale as is_cache_stale  # noqa: F401

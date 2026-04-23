"""
股票模糊搜索服务。

数据源：东财 searchapi.eastmoney.com 官方搜索接口
输入模式（三选一，自动识别）：
    1. 中文名    '贵州茅台'
    2. 拼音缩写  'gzmt' / 'mt'
    3. 代码/代码前缀  '600519' / '600'

输出仅保留 A 股（沪A/深A/京A），过滤掉港股/美股/期货/板块等。
每次调用打一次东财接口，响应约 200-400ms。
"""
import logging

from api_endpoints import eastmoney

logger = logging.getLogger(__name__)


def search_stocks(query, limit=20):
    """
    Returns:
        [{code, name, pinyin, marketType, marketPrefix, secid}, ...]
        - marketType: '沪A' / '深A' / '京A'
        - marketPrefix: 'SH' / 'SZ' / 'BJ'
        - secid: '1.600519' 东财格式，后续查行情用
    """
    query = (query or '').strip()
    if not query:
        return []

    try:
        # 多拉一些再前端 limit，因为 A 股过滤后可能剩不够
        raw = eastmoney.raw_em_search(query, page_size=min(limit * 3, 50))
    except Exception as e:
        logger.warning(f"股票搜索失败 query={query!r}: {e}")
        return []

    data = raw.get('Data') or []
    results = []
    for x in data:
        if x.get('Classify') != 'AStock':
            continue  # 过滤港股/美股/期货/板块等
        results.append({
            "code": str(x.get('Code') or ''),
            "name": str(x.get('Name') or ''),
            "pinyin": str(x.get('PinYin') or ''),
            "marketType": str(x.get('SecurityTypeName') or ''),
            "marketPrefix": str(x.get('MarketPrefix') or ''),
            "secid": str(x.get('QuoteID') or ''),
        })
        if len(results) >= limit:
            break
    return results

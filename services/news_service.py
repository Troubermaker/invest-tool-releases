"""
快讯服务（同花顺 / 东方财富）。

输出 schema 统一：
    [{
        id:        string,
        title:     string,
        summary:   string,    # 摘要正文
        time:      string,    # 显示时间 'YYYY-MM-DD HH:MM:SS'
        timestamp: int,       # Unix 秒，便于排序
        source:    'ths' | 'em',
        tags:      string[],  # 题材 / 板块 标签
        url:       string,    # 原文链接（可空）
        important: bool,      # 是否高亮（接口里"红色"标记）
    }, ...]
"""
import logging
from datetime import datetime

import db
from api_endpoints import tonghuashun, eastmoney

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = 'fast_news:'


def get_fast_news(source='ths', force=False):
    if source not in ('ths', 'em'):
        raise ValueError(f"source 必须是 'ths' 或 'em'，收到: {source}")

    cache_key = f"{CACHE_KEY_PREFIX}{source}"
    cached, updated_at = db.get_cache(cache_key)
    # 快讯不区分盘中/盘后：宏观、政策、夜盘新闻盘外才出，必须 24x7 都按 60s 刷
    # （默认 offhours_ttl=24h 不适用于快讯场景）
    if not force and cached and not db.is_market_cache_stale(
            updated_at, trading_ttl=60, offhours_ttl=60):
        return cached

    results = _get_ths() if source == 'ths' else _get_em()

    if not results:
        logger.info(f"{source} 快讯返回空，保留上次缓存")
        return cached if cached else []

    db.set_cache(cache_key, results)
    return results


def _get_ths():
    raw = tonghuashun.raw_ths_fast_news()
    items = (raw.get('data') or {}).get('list') or []
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        # ctime 是 Unix 秒字符串
        ts = 0
        try:
            ts = int(item.get('ctime') or 0)
        except (TypeError, ValueError):
            pass
        time_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else ''
        # tags 既有 'tag' 逗号串也有 'tags' 对象数组，优先 'tags'
        tag_list = []
        if isinstance(item.get('tags'), list):
            for t in item['tags']:
                if isinstance(t, dict) and t.get('name'):
                    tag_list.append(t['name'])
        if not tag_list and item.get('tag'):
            tag_list = [t.strip() for t in str(item['tag']).split(',') if t.strip()]
        out.append({
            'id':        str(item.get('id') or item.get('seq') or ''),
            'title':     str(item.get('title') or ''),
            'summary':   str(item.get('digest') or ''),
            'time':      time_str,
            'timestamp': ts,
            'source':    'ths',
            'tags':      tag_list,
            'url':       str(item.get('url') or ''),
            'important': str(item.get('color') or '0') == '1',
        })
    return out


def _get_em():
    raw = eastmoney.raw_em_fast_news()
    items = (raw.get('data') or {}).get('fastNewsList') or []
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get('code'):
            continue
        time_str = str(item.get('showTime') or '')
        # 把 'YYYY-MM-DD HH:MM:SS' 解析成 timestamp
        ts = 0
        try:
            ts = int(datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S').timestamp())
        except (TypeError, ValueError):
            pass
        # EM 的 stockList 是 ['90.BK0800', '1.600519'] secid 列表，作为 tag 不够友好，先不展示；
        # 把 stockList 作为标签数量提示
        tags = []
        # 没有现成的题材标签字段，留空
        out.append({
            'id':        str(item.get('code') or ''),
            'title':     str(item.get('title') or ''),
            'summary':   str(item.get('summary') or ''),
            'time':      time_str,
            'timestamp': ts,
            'source':    'em',
            'tags':      tags,
            'url':       '',  # EM 快讯没有原文页 URL
            'important': int(item.get('titleColor') or 0) != 0,
        })
    return out

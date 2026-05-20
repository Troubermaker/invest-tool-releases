"""
自选股批量导入：从粘贴文本 / OCR 文本里提取 A 股代码并补全名称。

支持识别的格式（统统能解析）：
    - 纯代码：'600519'、'sh600519'、'SH.600519'、'600519.SH'
    - 中文名：'贵州茅台'、'贵州茅台 600519'
    - 混排：'今天关注：贵州茅台600519，海康威视(sz002415)'
    - 多行/任意分隔符（空格、逗号、分号、换行、Tab、中文标点等）

OCR 容错：
    - 0 vs O、1 vs l、6 vs G 等常见误识，仅对纯代码做修正
    - 没识别到的中文名忽略
"""
import logging
import re

from services import stock_search_service
import symbols

logger = logging.getLogger(__name__)


# A 股代码：6 位数字
# 沪市：6xxxxx（主板 60、科创 688、603/605）
# 深市：00xxxx（主板/中小板）、3xxxxx（创业板）
# 北交所：4xxxxx、8xxxxx、920xxx
A_SHARE_CODE_RE = re.compile(r'\b(?<![\d])(\d{6})(?![\d])\b')

# 中文名候选：连续 2-8 个汉字（A 股名称大多 2-4 字，留余地到 8）
CHINESE_NAME_RE = re.compile(r'[一-龥]{2,8}')


def _is_valid_a_share_code(code):
    """是否是合法的 A 股代码段（6 位 + 合理前缀）。"""
    if not code or len(code) != 6 or not code.isdigit():
        return False
    p1 = code[0]
    p2 = code[:2]
    p3 = code[:3]
    return (p1 == '6'                                                  # 沪 A（含科创）
            or p2 in ('00', '30', '83', '87', '88')                    # 深 A / 创业板 / 北交所
            or p3 == '920'                                              # 新北交所代码段
            or p1 == '4' or p1 == '8')                                  # 老北交所代码段


def _ocr_fix(s):
    """对纯数字段做常见 OCR 误识修正（仅当上下文像代码时）。"""
    return (s.replace('O', '0').replace('o', '0')
             .replace('l', '1').replace('I', '1')
             .replace('G', '6').replace('B', '8'))


def parse_text(text, max_results=200):
    """
    从一段文本里识别 A 股代码 + 名字。
    返回 [{'code': '600519', 'name': '贵州茅台', 'source': 'code'|'name'|'both'}, ...]，按出现顺序去重。

    实现策略：
        1. 先正则抓所有 6 位代码（含 OCR 误识修正后再抓一遍）
        2. 用 search_stocks 给每个代码补全名字
        3. 残留无代码的中文名候选，挨个查 search_stocks 反向匹配
    """
    if not text:
        return []
    text = str(text)

    # 1) 抓代码（先原文，再 OCR 修复版）
    codes_in_order = []
    seen_codes = set()
    for m in A_SHARE_CODE_RE.finditer(text):
        c = m.group(1)
        if _is_valid_a_share_code(c) and c not in seen_codes:
            seen_codes.add(c)
            codes_in_order.append(c)
    # OCR 修复版（针对图片 OCR 后可能出现 O→0 等）
    fixed = _ocr_fix(text)
    if fixed != text:
        for m in A_SHARE_CODE_RE.finditer(fixed):
            c = m.group(1)
            if _is_valid_a_share_code(c) and c not in seen_codes:
                seen_codes.add(c)
                codes_in_order.append(c)

    results = []
    for code in codes_in_order[:max_results]:
        name = ''
        try:
            hits = stock_search_service.search_stocks(code, limit=3)
            for h in hits:
                if h.get('code') == code:
                    name = h.get('name', '')
                    break
        except Exception:
            pass
        results.append({'code': code, 'name': name, 'source': 'code'})

    # 2) 残留中文名候选（提取所有 2-4 字汉字段，去重，逐个反查）
    if len(results) < max_results:
        # 优先精炼名候选：剔除常见无关词
        STOPWORDS = {
            '今天', '昨天', '明天', '今日', '昨日', '上证', '深证', '指数', '股票',
            '关注', '买入', '卖出', '加仓', '减仓', '止损', '止盈', '涨停', '跌停',
            '观察', '关注', '推荐', '建议', '注意', '突破', '回调', '反弹',
            '市场', '板块', '主力', '资金', '量能', '形态', '走势',
        }
        name_candidates_seen = set()
        name_candidates = []
        for m in CHINESE_NAME_RE.finditer(text):
            cand = m.group(0)
            # A 股名长度通常 2-5（含 ST 前缀），先取前 5 字试
            for length in (5, 4, 3, 2):
                sub = cand[:length] if len(cand) >= length else cand
                if sub in STOPWORDS or len(sub) < 2:
                    continue
                if sub in name_candidates_seen:
                    continue
                name_candidates_seen.add(sub)
                name_candidates.append(sub)
                break

        for cand in name_candidates:
            if len(results) >= max_results:
                break
            try:
                hits = stock_search_service.search_stocks(cand, limit=3)
            except Exception:
                continue
            # 取第一条名字完全包含候选词的
            best = None
            for h in hits:
                if cand in (h.get('name') or ''):
                    best = h
                    break
            if not best and hits:
                # 没完全包含的话，再取首条作为最佳猜测
                best = hits[0]
            if best:
                code = best.get('code') or ''
                if code and code not in seen_codes and _is_valid_a_share_code(code):
                    seen_codes.add(code)
                    results.append({
                        'code': code,
                        'name': best.get('name', ''),
                        'source': 'name',
                    })

    return results


def batch_add_to_group(group_id, codes_with_names):
    """
    批量加入分组。已存在的 code 跳过；不会改动现有记录。
    Args:
        group_id: int
        codes_with_names: [{'code', 'name', 'price'?, 'added_at'?}]
            price 转 added_price；added_at 接受 ISO 字符串，未传则交给 SQLite 默认 CURRENT_TIMESTAMP
    Returns:
        {'added': int, 'skipped_existing': int, 'failed': int, 'detail': [...]}
    """
    from services import watchlist_service

    if not group_id or not codes_with_names:
        return {'added': 0, 'skipped_existing': 0, 'failed': 0, 'detail': []}

    # 当前组内已有 code（用来去重）
    try:
        existing = {s['code'] for s in watchlist_service.get_group_stocks(group_id)}
    except Exception:
        existing = set()

    added = 0
    skipped_existing = 0
    failed = 0
    detail = []
    for it in codes_with_names:
        code = (it.get('code') or '').strip()
        name = (it.get('name') or '').strip()
        if not code:
            continue
        if code in existing:
            skipped_existing += 1
            detail.append({'code': code, 'name': name, 'status': 'skipped'})
            continue
        price = it.get('price')
        added_at = it.get('added_at')
        try:
            watchlist_service.add_stock(group_id, code, name, price, '', added_at=added_at)
            existing.add(code)
            added += 1
            detail.append({'code': code, 'name': name, 'status': 'added'})
        except Exception as e:
            logger.warning(f'批量导入失败 {code}: {e}')
            failed += 1
            detail.append({'code': code, 'name': name, 'status': 'failed', 'error': str(e)})
    return {
        'added': added,
        'skipped_existing': skipped_existing,
        'failed': failed,
        'detail': detail,
    }

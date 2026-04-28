"""
价格警报服务。

职责：
    1. set_alert(code, above, below) / clear_alert(code) —— 用户在 UI 上设/清警报阈值
    2. check_alerts() —— scheduler 每 30s 调一次，遍历所有有警报的自选股，命中阈值就触发
    3. 触发时写 alert_history（去重 1h），同时尝试桌面通知（plyer）
    4. get_pending_alerts() / ack_alerts(ids) —— 前端轮询用，拿到未展示的列表 + 标记已展示

去重策略：
    同 code+alert_type 在 1h 内只触发一次，避免连续命中阈值反复打扰。
    用户清空 alert_history 后下次命中又能触发（手动重置）。

桌面通知：
    用 plyer.notification（跨平台），失败时静默降级（前端 toast 仍能正常工作）。
"""
import logging
from datetime import datetime, timedelta

import db
from services import quote_service, watchlist_service

logger = logging.getLogger(__name__)

# 同警报 1h 内不重复触发
_DEDUP_WINDOW = timedelta(hours=1)

# 偏好键：桌面通知（Windows Toast）开关；默认 False = 仅应用内 toast
_PREF_DESKTOP_NOTIFY = 'alerts.desktop_notification'


# ============ 设置 / 取消警报 ============

def set_alert(code, above=None, below=None):
    """
    给某只自选股设警报阈值。
    Args:
        code:  股票代码
        above: 现价 ≥ 此值时触发（可为 None = 不设上涨警报）
        below: 现价 ≤ 此值时触发（可为 None = 不设下跌警报）
    会写到所有该 code 的 watchlist_stocks 行（多分组里同一只股共享一个警报）。
    """
    above_v = float(above) if above not in (None, '', 0) else None
    below_v = float(below) if below not in (None, '', 0) else None
    conn = db.get_db()
    conn.execute(
        'UPDATE watchlist_stocks SET alert_above = ?, alert_below = ? WHERE code = ?',
        (above_v, below_v, code),
    )
    conn.commit()
    conn.close()
    return {'code': code, 'alert_above': above_v, 'alert_below': below_v}


def clear_alert(code):
    """清除某只股的警报阈值。"""
    return set_alert(code, above=None, below=None)


def get_alert(code):
    """读取单只股的警报设置。返回 {alert_above, alert_below} 或 None。"""
    conn = db.get_db()
    row = conn.execute(
        'SELECT alert_above, alert_below FROM watchlist_stocks WHERE code = ? LIMIT 1',
        (code,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {'alert_above': row['alert_above'], 'alert_below': row['alert_below']}


# ============ 检查 + 触发 ============

def check_alerts():
    """
    扫描所有设了警报的自选股，命中触发。被 scheduler 周期性调用。
    返回触发数量（便于日志统计）。
    """
    conn = db.get_db()
    rows = conn.execute('''
        SELECT DISTINCT code, name, alert_above, alert_below
        FROM watchlist_stocks
        WHERE alert_above IS NOT NULL OR alert_below IS NOT NULL
    ''').fetchall()
    conn.close()
    if not rows:
        return 0

    codes = [r['code'] for r in rows]
    try:
        quotes_map = quote_service.get_batch_quotes(codes)
    except Exception as e:
        logger.warning(f'警报检查时拉行情失败: {e}')
        return 0

    triggered = 0
    for r in rows:
        code = r['code']
        q = quotes_map.get(code)
        if not q or q.get('price') is None:
            continue
        price = float(q['price'])
        name = r['name'] or q.get('name', '') or code

        if r['alert_above'] is not None and price >= r['alert_above']:
            if _trigger_alert(code, name, 'above', r['alert_above'], price):
                triggered += 1
        if r['alert_below'] is not None and price <= r['alert_below']:
            if _trigger_alert(code, name, 'below', r['alert_below'], price):
                triggered += 1

    if triggered:
        logger.info(f'警报检查：触发 {triggered} 条')
    return triggered


def _trigger_alert(code, name, alert_type, threshold, triggered_price):
    """
    单次触发：先去重判断，未在 1h 内触发过才写 history + 推通知。
    返回 True 表示真触发了（用于计数）；False 表示被去重吃掉。
    """
    if _recently_triggered(code, alert_type):
        return False

    conn = db.get_db()
    conn.execute('''
        INSERT INTO alert_history
            (code, name, alert_type, threshold, triggered_price, triggered_at, acked)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    ''', (code, name, alert_type, threshold, triggered_price, datetime.now().isoformat()))
    conn.commit()
    conn.close()

    # 异步推桌面通知（失败不影响主流程）
    try:
        _send_desktop_notification(code, name, alert_type, threshold, triggered_price)
    except Exception as e:
        logger.warning(f'桌面通知发送失败: {e}')

    return True


def _recently_triggered(code, alert_type):
    """同 code+type 在去重窗口内是否已经触发过。"""
    cutoff = (datetime.now() - _DEDUP_WINDOW).isoformat()
    conn = db.get_db()
    row = conn.execute('''
        SELECT 1 FROM alert_history
        WHERE code = ? AND alert_type = ? AND triggered_at >= ?
        LIMIT 1
    ''', (code, alert_type, cutoff)).fetchone()
    conn.close()
    return bool(row)


# ============ 前端轮询接口 ============

def get_pending_alerts(limit=20):
    """
    返回未 ack 的警报列表（按时间倒序）。前端轮询用。
    """
    conn = db.get_db()
    rows = conn.execute('''
        SELECT id, code, name, alert_type, threshold, triggered_price, triggered_at
        FROM alert_history
        WHERE acked = 0
        ORDER BY triggered_at DESC
        LIMIT ?
    ''', (limit,)).fetchall()
    conn.close()
    return [
        {
            'id':              r['id'],
            'code':            r['code'],
            'name':            r['name'],
            'alert_type':      r['alert_type'],     # 'above' | 'below'
            'threshold':       r['threshold'],
            'triggered_price': r['triggered_price'],
            'triggered_at':    r['triggered_at'],
        }
        for r in rows
    ]


def ack_alerts(ids):
    """前端展示完成后调，把这些警报标记成 acked，下次轮询不再返回。"""
    if not ids:
        return 0
    placeholders = ','.join('?' * len(ids))
    conn = db.get_db()
    cur = conn.execute(
        f'UPDATE alert_history SET acked = 1 WHERE id IN ({placeholders})',
        ids,
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


# ============ 桌面通知 ============

_PLYER_AVAILABLE = None    # 探测一次后缓存结果
_PLYER_NOTIFIER = None


def _send_desktop_notification(code, name, alert_type, threshold, triggered_price):
    """
    用 plyer 推 Windows 原生 Toast。
    用户在 Settings 没勾"桌面通知"的话直接跳过（默认即不启用），
    plyer 没装/失败时也静默降级——应用内 toast 永远工作。
    """
    # 读用户偏好；默认关
    if not watchlist_service.get_preference(_PREF_DESKTOP_NOTIFY, False):
        return

    global _PLYER_AVAILABLE, _PLYER_NOTIFIER
    if _PLYER_AVAILABLE is False:
        return
    if _PLYER_NOTIFIER is None:
        try:
            from plyer import notification as _n
            _PLYER_NOTIFIER = _n
            _PLYER_AVAILABLE = True
        except ImportError:
            logger.info('plyer 未安装，跳过桌面通知（pip install plyer 可启用）')
            _PLYER_AVAILABLE = False
            return

    direction = '↑ 上涨' if alert_type == 'above' else '↓ 下跌'
    title = f'股价提醒 · {name or code}'
    msg = f'{direction}至 {triggered_price:.2f}（阈值 {threshold:.2f}）'
    try:
        _PLYER_NOTIFIER.notify(
            title=title, message=msg,
            app_name='InvestTool', timeout=10,
        )
    except Exception as e:
        logger.warning(f'plyer notify 失败: {e}')

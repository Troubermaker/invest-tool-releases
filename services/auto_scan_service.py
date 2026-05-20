"""
每日自动扫描结果持久化 + 桌面推送（P1）。

工作流：
  1. 前端 useDailyAutoScan composable 在 09:25+ 时段触发扫描
  2. 扫完过滤 ⭐⭐⭐+ → 调 save_result 持久化
  3. 调 send_desktop_notification 推 toast（plyer）
  4. UI banner / 任务栏图标 显示今日命中数
"""
import json
import logging
from datetime import date, datetime

import db

logger = logging.getLogger(__name__)


def save_result(scan_date, star_count, star4_count, top_codes, total_scanned, notes=''):
    """保存一次自动扫描结果（同日已存则覆盖）。"""
    if not scan_date:
        scan_date = date.today().isoformat()
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO auto_scan_results
            (scan_date, star_count, star4_count, top_codes, total_scanned, scanned_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        scan_date,
        int(star_count or 0),
        int(star4_count or 0),
        json.dumps(top_codes or [], ensure_ascii=False),
        int(total_scanned or 0),
        datetime.now().isoformat(),
        notes or '',
    ))
    conn.commit()
    conn.close()
    return scan_date


def get_by_date(d):
    """按日期取一行扫描结果。"""
    if not d:
        d = date.today().isoformat()
    conn = db.get_db()
    c = conn.cursor()
    c.execute('SELECT scan_date, star_count, star4_count, top_codes, total_scanned, scanned_at, notes '
              'FROM auto_scan_results WHERE scan_date = ?', (d,))
    r = c.fetchone()
    conn.close()
    if not r: return None
    try:
        top = json.loads(r[3]) if r[3] else []
    except Exception:
        top = []
    return {
        'scan_date':     r[0],
        'star_count':    r[1],
        'star4_count':   r[2],
        'top_codes':     top,
        'total_scanned': r[4],
        'scanned_at':    r[5],
        'notes':         r[6] or '',
    }


def get_today():
    return get_by_date(date.today().isoformat())


def list_recent(limit=30):
    """最近 N 天扫描记录（按日倒序，元数据列表）。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('SELECT scan_date, star_count, star4_count, total_scanned, scanned_at '
              'FROM auto_scan_results ORDER BY scan_date DESC LIMIT ?', (int(limit),))
    rows = c.fetchall()
    conn.close()
    return [
        {
            'scan_date':     r[0],
            'star_count':    r[1],
            'star4_count':   r[2],
            'total_scanned': r[3],
            'scanned_at':    r[4],
        }
        for r in rows
    ]


def send_desktop_notification(title, message, timeout=10):
    """
    系统桌面通知（跨 Windows / macOS）。
    用 plyer（已在 requirements.txt）。失败静默（不阻塞主流程）。
    """
    try:
        from plyer import notification
        notification.notify(
            title=str(title)[:80],
            message=str(message)[:200],
            timeout=int(timeout),
            app_name='Invest Tool',
        )
        return True
    except Exception as e:
        logger.warning(f'desktop notification failed: {e}')
        return False

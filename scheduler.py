"""
定时任务：每个交易日 15:05 做 EOD (End of Day) 数据快照。

目的：收盘后把"指数 / 热门板块 / 板块联动股票 / 连板天梯 / 市场情绪"全部抓取一次
并写入本地缓存；配合 db.is_market_cache_stale 的 24h 非交易时段 TTL，
非交易时段（晚上/周末）就完全不需要再请求第三方接口了，降低被反爬的风险。

时间线：
    09:30-11:35 / 13:00-15:00   交易时段，1 分钟 TTL，正常抓取实时数据
    15:05                        EOD 快照任务触发：force=True 强制重抓 + 写入缓存
    15:05 当日晚 ~ 次日 09:30    使用 24h TTL 命中快照，零请求

设计要点：
    - 各 service 的 force=True 会跳过缓存直接抓 → 成功时服务内部 set_cache 覆盖旧值
    - 抓取失败时原有缓存不被破坏（不再像旧版那样"先清后抓"有失败风险）
"""
import threading
import time
from datetime import datetime

import schedule

import db
from services import (
    market_service,
    sector_service,
    sector_stocks_service,
    limit_up_ladder_service,
    market_sentiment_service,
)

# 前 N 个热门板块的"联动股票列表"也做快照（按 KPL rank 顺序）
SECTOR_STOCKS_SNAPSHOT_TOP_N = 15


def _log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Scheduler] {msg}")


def job_eod_snapshot():
    """每个交易日 15:05 执行的 EOD 快照任务。节假日 / 周末 / 调休不开市日自动跳过。"""
    today = datetime.now().date()
    if not db.is_trading_day(today):
        _log(f"Skip EOD snapshot ({today} 非交易日)")
        return

    _log("EOD snapshot starting...")

    # ① 固定项：指数 / 天梯 / 市场情绪
    tasks = [
        ('市场指数', lambda: market_service.get_market_indices(force=True)),
        ('连板天梯', lambda: limit_up_ladder_service.get_ladder(force=True)),
        ('市场情绪', lambda: market_sentiment_service.get_sentiment(force=True)),
    ]
    for name, fn in tasks:
        try:
            fn()
            _log(f"  ✓ {name}")
        except Exception as e:
            _log(f"  ✗ {name}: {e}")

    # ② 热门板块 + 前 N 个板块的联动股票
    try:
        sectors = sector_service.get_hot_sectors(force=True) or []
        _log(f"  ✓ 热门板块 ({len(sectors)} 个)")
        ok = 0
        for sector in sectors[:SECTOR_STOCKS_SNAPSHOT_TOP_N]:
            plate_id = sector.get('code')
            if not plate_id:
                continue
            try:
                sector_stocks_service.get_sector_stocks(plate_id, force=True)
                ok += 1
            except Exception as e:
                _log(f"    sector_stocks({plate_id}) failed: {e}")
        _log(f"  ✓ 板块联动股票 ({ok}/{min(len(sectors), SECTOR_STOCKS_SNAPSHOT_TOP_N)})")
    except Exception as e:
        _log(f"  ✗ 热门板块: {e}")

    _log("EOD snapshot done.")


def run_scheduler():
    # 15:05 给 A 股收盘预留 5 分钟，确保数据落盘
    schedule.every().day.at("15:05").do(job_eod_snapshot)
    _log("Started background daemon. EOD snapshot scheduled at 15:05 on weekdays.")
    while True:
        schedule.run_pending()
        time.sleep(60)  # 分钟粒度轮询


def start_background_daemon():
    """Start the scheduler in a background daemon thread so it doesn't block pywebview"""
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()


if __name__ == "__main__":
    job_eod_snapshot()  # 直接运行文件时立刻执行一次，方便手动补快照

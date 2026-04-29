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
from datetime import datetime, time as dtime

import schedule

import db
from services import (
    market_service,
    sector_service,
    sector_stocks_service,
    limit_up_ladder_service,
    market_sentiment_service,
    alert_service,
)

# 关键缓存 key：启动预热时检测哪些需要补
_KEY_CACHES = ['market_indices', 'hot_sectors_kpl', 'limit_up_ladder', 'market_sentiment']

# 前 N 个热门板块的"联动股票列表"也做快照（按 KPL rank 顺序）
SECTOR_STOCKS_SNAPSHOT_TOP_N = 15


def _log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Scheduler] {msg}")


def _run_full_fetch(label):
    """
    实际的 force 抓取逻辑（无交易日校验）。
    EOD 任务和启动预热都复用这个。失败不破坏旧缓存（service 层 force=True + 空不写）。
    """
    _log(f"{label} 开始...")

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

    _log(f"{label} 完成。")


def job_eod_snapshot():
    """每个交易日 15:05 执行的 EOD 快照任务。非交易日自动跳过。"""
    today = datetime.now().date()
    if not db.is_trading_day(today):
        _log(f"Skip EOD snapshot ({today} 非交易日)")
        return
    _run_full_fetch("EOD snapshot")


def job_alert_check():
    """
    周期性检查价格警报。每 30s 跑一次。
    盘外 quote_service 走缓存（上次拉的快照），不会真的调 EM；
    所以这个 job 不限制工作日/session — 让用户在任何时候设警报都能立即被触发到。
    """
    try:
        n = alert_service.check_alerts()
        if n > 0:
            _log(f"价格警报触发 {n} 条")
    except Exception as e:
        _log(f"警报检查异常: {e}")


def warm_cache_on_startup_async():
    """
    启动时按需预热缓存（在后台线程跑，不阻塞窗口启动）。
    策略：
      - 盘中 (9:30-11:35 / 13:00-15:00 工作日) → 不主动预热（实时抓取够用）
      - 其它时段（盘前 / 盘后 / 周末 / 节假日）：检查关键缓存是否需要补
        - 缺失或已过期 → 后台触发完整抓取
        - 全部新鲜 → 跳过

    非交易日也会跑：因为新浪 / KPL 在周末会返回最近交易日数据（Friday 收盘）；
    THS 的天梯 / 情绪在周末返回空，但 service 已做"空不写缓存"保护，不会污染历史数据。
    """
    threading.Thread(target=_warm_cache_check_and_fire, daemon=True).start()


def _warm_cache_check_and_fire():
    now = datetime.now()
    is_td = db.is_trading_day(now.date())
    is_intraday = is_td and (
        (dtime(9, 30) <= now.time() <= dtime(11, 35)) or
        (dtime(13, 0) <= now.time() <= dtime(15, 0))
    )
    if is_intraday:
        _log("启动预热：盘中跳过（实时抓取够用）")
        return

    needs_warm = False
    for key in _KEY_CACHES:
        cached, ts = db.get_cache(key)
        if not cached or db.is_market_cache_stale(ts):
            needs_warm = True
            break

    if not needs_warm:
        _log("启动预热：关键缓存已就绪，跳过")
        return

    label = '启动预热（交易日盘后）' if is_td else '启动预热（非交易日，抓取最近交易日数据）'
    _run_full_fetch(label)


def run_scheduler():
    # 15:05 给 A 股收盘预留 5 分钟，确保数据落盘
    schedule.every().day.at("15:05").do(job_eod_snapshot)
    # 价格警报：每 30s 检查一次，job 内部判断是否在交易时段
    schedule.every(30).seconds.do(job_alert_check)
    _log("Started: EOD snapshot @15:05；价格警报检查每 30s")
    while True:
        schedule.run_pending()
        time.sleep(5)  # 5s 轮询，让 30s alert check 触发足够准


def start_background_daemon():
    """Start the scheduler in a background daemon thread so it doesn't block pywebview"""
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()


if __name__ == "__main__":
    job_eod_snapshot()  # 直接运行文件时立刻执行一次，方便手动补快照

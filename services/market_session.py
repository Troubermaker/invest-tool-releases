"""
A 股市场会话（session）单一来源真相。

跟前端 useSmartRefresh.js 的 marketSession computed 保持**字段名 / 边界完全一致**。
两边都改时间边界要同步更新（前后端共用同一份语义）。

== 8 档会话 ==
  weekend           周六/周日全天                       — 暂停
  holiday           工作日里的法定节假日（清明/国庆等）  — 暂停
  before-open       0:00  - 9:15                       — 暂停
  auction           9:15  - 9:25  集合竞价（行情有变动） — 仍是 trading
  auction-quiet     9:25  - 9:30  撮合期（行情冻结）    — trading（缓存有效）但前端不轮询
  morning           9:30  - 11:30 上半场                — trading
  lunch             11:30 - 13:00 午休                  — 暂停
  afternoon         13:00 - 15:00 下半场                — trading
  after-close       15:00 - 24:00 盘后                  — 暂停

== 两个布尔判定 ==
  is_trading_hours(now)  → bool   缓存 TTL 用：data 可能正在变动的时段
                                   返 True 即 cache 应走 trading_ttl（短，如 10s）
                                   返 False 即 cache 走 offhours_ttl（长，如 24h）
  is_polling_active(now) → bool   前端 useSmartRefresh 该不该主动轮询
                                   = is_trading_hours 减掉 auction-quiet
                                   （撮合期行情冻结，没必要每 3s 打接口）

修复历史：原 db.is_market_cache_stale 把 9:15-9:30 集合竞价误判为 offhours，导致
集合竞价期间显示昨天的涨跌幅。此模块把判定统一到「auction 也算 trading_hours」。
"""
from datetime import datetime, time as dtime

# ---------------- 会话边界（跟前端 useSmartRefresh.js 完全一致）----------------
_AUCTION_START   = dtime(9, 15)
_AUCTION_END     = dtime(9, 25)
_AUCTION_QUIET_END = dtime(9, 30)
_MORNING_END     = dtime(11, 30)
_AFTERNOON_START = dtime(13, 0)
_MARKET_CLOSE    = dtime(15, 0)


SESSION_WEEKEND       = 'weekend'
SESSION_HOLIDAY       = 'holiday'
SESSION_BEFORE_OPEN   = 'before-open'
SESSION_AUCTION       = 'auction'
SESSION_AUCTION_QUIET = 'auction-quiet'
SESSION_MORNING       = 'morning'
SESSION_LUNCH         = 'lunch'
SESSION_AFTERNOON     = 'afternoon'
SESSION_AFTER_CLOSE   = 'after-close'


def get_session(now=None):
    """返回当前市场会话（8 档枚举之一）。"""
    if now is None:
        now = datetime.now()

    weekday = now.weekday()
    if weekday >= 5:
        return SESSION_WEEKEND

    # 节假日判定（避开循环 import，惰性引入 db 模块）
    try:
        import db
        if not db.is_trading_day(now.date()):
            return SESSION_HOLIDAY
    except Exception:
        pass  # 库不可用时不返 holiday，照常按时段判，无害

    t = now.time()
    if t < _AUCTION_START:        return SESSION_BEFORE_OPEN
    if t < _AUCTION_END:          return SESSION_AUCTION
    if t < _AUCTION_QUIET_END:    return SESSION_AUCTION_QUIET
    if t < _MORNING_END:          return SESSION_MORNING
    if t < _AFTERNOON_START:      return SESSION_LUNCH
    if t <= _MARKET_CLOSE:        return SESSION_AFTERNOON
    return SESSION_AFTER_CLOSE


# 「行情数据可能正在变动」的会话集合
# auction + auction-quiet 都算 trading（auction-quiet 期间 EM 接口仍返撮合价）
_TRADING_SESSIONS = {
    SESSION_AUCTION,
    SESSION_AUCTION_QUIET,
    SESSION_MORNING,
    SESSION_AFTERNOON,
}

# 「前端该主动轮询」的会话集合（排除撮合静默期）
_POLLING_SESSIONS = {
    SESSION_AUCTION,
    SESSION_MORNING,
    SESSION_AFTERNOON,
}


def is_trading_hours(now=None):
    """缓存 TTL 用：行情可能变动的时段返 True。"""
    return get_session(now) in _TRADING_SESSIONS


def is_polling_active(now=None):
    """前端轮询用：跟前端 isMarketOpen 对齐。"""
    return get_session(now) in _POLLING_SESSIONS

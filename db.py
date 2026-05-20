import sqlite3
import os
import sys
import json
import shutil
from datetime import datetime, time as dtime, timedelta


def _resolve_data_dir():
    """
    返回放 invest_data.db 的目录。

    - 开发态（python main.py）：项目根目录（与 db.py 同级），便于查看 / 调试
    - 打包态（dist/.../invest_tool.exe）：从 app_config 读，默认 %APPDATA%\\InvestTool\\
        用户在 Settings 里改了路径会写到 config.json，下次启动这里读到新路径

    打包态自动迁移：旧版可能把 db 写在 _internal/ 或 exe 同级，启动时自动搬到 data_dir。
    """
    if getattr(sys, 'frozen', False):
        # 从 app_config 读用户配置（含默认值回落）
        import app_config
        data_dir = app_config.get_data_dir()
        os.makedirs(data_dir, exist_ok=True)

        # 兼容迁移：旧版残留的 invest_data.db 自动搬到 data_dir
        target_db = os.path.join(data_dir, 'invest_data.db')
        if not os.path.exists(target_db):
            for legacy_dir in (
                os.path.dirname(os.path.abspath(__file__)),  # _internal/
                os.path.dirname(sys.executable),              # exe 同级
            ):
                legacy_db = os.path.join(legacy_dir, 'invest_data.db')
                if os.path.exists(legacy_db) and os.path.abspath(legacy_db) != os.path.abspath(target_db):
                    try:
                        shutil.copy2(legacy_db, target_db)
                        break
                    except OSError:
                        pass

        return data_dir

    # 开发态：项目根目录
    return os.path.dirname(os.path.abspath(__file__))


DB_PATH = os.path.join(_resolve_data_dir(), 'invest_data.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # WAL（Write-Ahead Logging）模式：让多个连接可同时读 + 单写者，
    # scheduler 后台线程和 API 主线程并发写不会互相阻塞 / 损坏数据库。
    # PRAGMA 是连接级设置，但 WAL 模式一旦设置会持久化在 DB 文件元信息里，
    # 后续所有新连接默认都是 WAL，重复设置无害。
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    # 市场行情缓存表
    c.execute('''
        CREATE TABLE IF NOT EXISTS market_cache (
            key TEXT PRIMARY KEY,
            data TEXT,
            updated_at TIMESTAMP
        )
    ''')
    # 旧单表自选（保留兼容；新代码用下面的 watchlist_groups/watchlist_stocks）
    c.execute('''
        CREATE TABLE IF NOT EXISTS watchlist (
            code TEXT PRIMARY KEY,
            name TEXT,
            added_at TIMESTAMP
        )
    ''')
    # 自选分组
    c.execute('''
        CREATE TABLE IF NOT EXISTS watchlist_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 自选分组下的股票
    c.execute('''
        CREATE TABLE IF NOT EXISTS watchlist_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            added_price REAL,
            remark TEXT DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(group_id, code),
            FOREIGN KEY (group_id) REFERENCES watchlist_groups(id) ON DELETE CASCADE
        )
    ''')
    # 迁移旧表：给已存在的 watchlist_stocks 表补字段
    for col, decl in [
        ('added_price',  'REAL'),
        ('remark',       "TEXT DEFAULT ''"),
        # 价格警报：alert_above 上涨触发阈值，alert_below 下跌触发阈值，任一可空
        ('alert_above',  'REAL'),
        ('alert_below',  'REAL'),
    ]:
        try:
            c.execute(f'ALTER TABLE watchlist_stocks ADD COLUMN {col} {decl}')
        except Exception:
            pass  # 已存在则忽略

    # 警报触发历史（用于去重 + 前端轮询展示）
    c.execute('''
        CREATE TABLE IF NOT EXISTS alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            alert_type TEXT NOT NULL,        -- 'above' | 'below'
            threshold REAL NOT NULL,         -- 设定的阈值
            triggered_price REAL NOT NULL,   -- 实际触发时的价格
            triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            acked INTEGER NOT NULL DEFAULT 0  -- 0 = 前端尚未展示，1 = 已展示过
        )
    ''')
    # 加索引方便查最近触发（去重 1h 窗口）和 un-acked 列表
    c.execute('CREATE INDEX IF NOT EXISTS idx_alert_code_time ON alert_history(code, alert_type, triggered_at)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_alert_unacked ON alert_history(acked, triggered_at)')
    # 用户偏好键值存储（列顺序、主题等）
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_preferences (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 持仓账户（多账户分组，和自选分组同构）
    c.execute('''
        CREATE TABLE IF NOT EXISTS portfolio_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 持仓记录：快照式，仅存 持股/成本价，市值/盈亏由前端配合实时行情算
    c.execute('''
        CREATE TABLE IF NOT EXISTS portfolio_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            shares INTEGER NOT NULL,
            cost_price REAL NOT NULL,
            remark TEXT DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id, code),
            FOREIGN KEY (account_id) REFERENCES portfolio_accounts(id) ON DELETE CASCADE
        )
    ''')

    # 候选池：找发车 / 找候选扫出来的票，用户主动 ⭐ 收藏后存这里
    # 跟 watchlist 是两回事 —— 这里专为「持续追踪买点」设计：
    #   - save_price / break_level / golden_price 是入选时的价格快照，永远不变
    #   - 当前价 vs 这些 snapshot 算出"等待中 / 临门一脚 / 已突破 / 进入买点 / 已失效"
    # UNIQUE(code) 保证同一只票只保留一条；重新保存覆盖旧记录（snapshot 重置）
    c.execute('''
        CREATE TABLE IF NOT EXISTS candidate_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT,
            saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            stage INTEGER,                              -- 1 蓄势中 / 2 试盘后等突破
            save_price REAL,                            -- 入选时收盘价（参考点）
            break_level REAL,                           -- 突破触发位（s1Upper）
            golden_price REAL,                          -- 黄金买点（推荐回踩位）
            s1_lower REAL,                              -- Stage 1 下沿（跌破 = 已失效）
            consolidation_bars INTEGER,                 -- 入选时蓄势已持续根数
            source TEXT DEFAULT '三维启动找候选',         -- 来源标签（未来支持多种扫描器）
            note TEXT DEFAULT '',                       -- 用户备注
            -- Phase 5：动态追踪字段（前端定期跑 detector 刷新写入；旧记录全 NULL，刷新后填充）
            peak_gain_since_save REAL,                  -- 收藏后历史最大涨幅 %（看是否抓到主升）
            formation_state TEXT,                       -- detector 判定的形态状态：
                                                        -- consolidating / tested / breakout / rally / exhausted / invalid
            last_refreshed_at TIMESTAMP,                -- 上次刷新时间（数据新鲜度）
            -- Phase 6：二次买点（突破后回踩不破 + 反包阳线）
            secondary_entry_at TIMESTAMP,               -- 反包K的时间（NULL = 没出现 / 未刷新）
            secondary_entry_price REAL,                 -- 反包K收盘价（用户参考介入点位）
            -- 突破日跟踪：detector 跑出的最新 stage 3 突破 K 时间（仅 stage 3 / rally / exhausted / invalid 有值）
            breakout_at TIMESTAMP,                      -- s3Time，前端用来算"距今 N 天"
            -- Phase 3 Step 2：ML 模型 T+7 盈利预测分数（0-1）
            -- 仅突破态（breakout/rally/stretched）有值；阈值 0.40 触发 ⭐ 优选
            ml_score REAL,
            -- Week 2 Day 1：N+1 突破确认（'strong'|'medium'|'fail'|'pending'|NULL）
            breakout_confirm TEXT,
            -- P2 龙虎榜（30 天窗口）
            lhb_in_window INTEGER,                       -- 0/1 是否上过榜
            lhb_count INTEGER,                           -- 30 天上榜次数
            lhb_net_buy REAL,                            -- 累计净买额（元）
            -- 综合星级 0-4（refresh 时 getTradeStarLevel 计算 + 持久化）
            star_level INTEGER
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_candidate_saved_at ON candidate_picks(saved_at DESC)')

    # Phase 5/6/7 迁移：给老库的 candidate_picks 表补字段（新建库 CREATE TABLE 已含）
    # SQLite 不支持 IF NOT EXISTS for ADD COLUMN，所以 try/except 兜底
    for migration in (
        'ALTER TABLE candidate_picks ADD COLUMN peak_gain_since_save REAL',
        'ALTER TABLE candidate_picks ADD COLUMN formation_state TEXT',
        'ALTER TABLE candidate_picks ADD COLUMN last_refreshed_at TIMESTAMP',
        'ALTER TABLE candidate_picks ADD COLUMN secondary_entry_at TIMESTAMP',
        'ALTER TABLE candidate_picks ADD COLUMN secondary_entry_price REAL',
        'ALTER TABLE candidate_picks ADD COLUMN breakout_at TIMESTAMP',
        'ALTER TABLE candidate_picks ADD COLUMN ml_score REAL',
        'ALTER TABLE candidate_picks ADD COLUMN breakout_confirm TEXT',
        'ALTER TABLE candidate_picks ADD COLUMN lhb_in_window INTEGER',
        'ALTER TABLE candidate_picks ADD COLUMN lhb_count INTEGER',
        'ALTER TABLE candidate_picks ADD COLUMN lhb_net_buy REAL',
        'ALTER TABLE candidate_picks ADD COLUMN star_level INTEGER',
    ):
        try:
            c.execute(migration)
        except Exception:
            pass   # 列已存在 → 忽略

    # ========= Phase 1 Day 3：回测持久化 =========
    # backtest_runs：每次跑 bt.runAll / bt.gridSearch / bt.runV0 后存一条
    # 后续可对比"调参前后"的胜率变化，避免凭直觉调参
    c.execute('''
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            run_type TEXT,                          -- 'runAll' | 'gridSearch' | 'runV0' | 'runExitExperiment'
            sample_size INTEGER,                    -- 实际扫描的股票数
            hold_days INTEGER,
            boards TEXT,                            -- JSON array: ['sh_main', 'sz_main', 'sme']
            detector_opts TEXT,                     -- JSON: 调用时显式传入的 detect opts
                                                    -- gridSearch 时含网格定义；其它含单个 config
            summary TEXT,                           -- JSON: aggregate stats (overall + byGrade + byWeekly + ...)
                                                    -- gridSearch 时含每个 combo 的 summary
            top_combos TEXT,                        -- 仅 gridSearch：JSON top 10 combos by win%
            notes TEXT DEFAULT ''                   -- 用户后期备注（调参意图 / 验证结论等）
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_backtest_runs_at ON backtest_runs(run_at DESC)')

    # 智能重训关联：记录 run 产生的 dataset 文件 + 训练出的 model 文件，
    # 删除 run 时可一并清理文件
    for migration in (
        'ALTER TABLE backtest_runs ADD COLUMN produced_dataset TEXT',
        'ALTER TABLE backtest_runs ADD COLUMN produced_model   TEXT',
    ):
        try:
            c.execute(migration)
        except Exception:
            pass

    # ========= P0 交易日志（真实交易闭环 + 仓位管理 + 实绩对比）=========
    # 跟候选池的"paper trading"正交：候选池是观察哨，trades_journal 是实战账本。
    # 用途：1) 实盘真实胜率追踪 vs 回测预期  2) 仓位 / 风险管理  3) 归因分析
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            -- 信号来源（哪个 tab / 哪条规则触发的买入）
            signal_source TEXT,                  -- 'main_breakout' | 'breakout_eve' | 'dragon_return' | 'limit_up_relay' | 'manual'
            star_level INTEGER,                  -- 0-3：⭐ 数量（手动买入 = 0）
            signal_metadata TEXT,                -- JSON：买入时的 breakoutConfirm / sectorScore / mlScore 等快照
            -- 买入
            entry_at TIMESTAMP NOT NULL,
            entry_price REAL NOT NULL,
            position_pct REAL,                   -- 占总资金 %（如 10 表示 10%）
            -- 计划价位（买入时由系统/用户填写）
            target_price REAL,                   -- 目标止盈位
            stop_loss REAL,                      -- 计划止损位
            -- 卖出（持仓中时这些为 NULL）
            exit_at TIMESTAMP,
            exit_price REAL,
            exit_reason TEXT,                    -- 'stop_loss' | 'take_profit' | 'time_out' | 'manual' | 'invalid'
            -- 收益
            pnl_pct REAL,                        -- 实际收益 %（自动算）
            hold_days INTEGER,                   -- 持仓天数（自动算）
            -- 备注
            notes TEXT DEFAULT '',
            -- 元数据
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_trades_journal_entry_at ON trades_journal(entry_at DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_trades_journal_code ON trades_journal(code)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_trades_journal_signal ON trades_journal(signal_source, star_level)')

    # ========= P2 龙虎榜数据（每日盘后增量更新）=========
    # 用途：作为 ML 特征，识别"真主力进场"vs"散户追涨"。
    # 业内实测：龙虎榜净买信号能给突破策略 +15-20pp 胜率。
    c.execute('''
        CREATE TABLE IF NOT EXISTS lhb_records (
            code TEXT NOT NULL,
            date TEXT NOT NULL,                 -- YYYY-MM-DD
            name TEXT,
            net_buy REAL,                       -- 龙虎榜净买额（元，正=机构净买）
            buy_amt REAL,                       -- 买入额（元）
            sell_amt REAL,                      -- 卖出额（元）
            deal_amt REAL,                      -- 龙虎榜总成交额
            explain TEXT,                       -- 解读（如"3家机构买入，成功率68%"）
            change_rate REAL,                   -- 当日涨跌幅 %
            close_price REAL,
            inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (code, date)
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_lhb_date ON lhb_records(date DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_lhb_code_date ON lhb_records(code, date DESC)')

    # ========= 每日自动扫描结果（盘前推送） =========
    # 每天一行，存当天 ⭐⭐⭐+ 信号摘要，供 banner / 通知用
    c.execute('''
        CREATE TABLE IF NOT EXISTS auto_scan_results (
            scan_date TEXT PRIMARY KEY,            -- YYYY-MM-DD
            star_count INTEGER NOT NULL,           -- ⭐⭐⭐+ 数量
            star4_count INTEGER DEFAULT 0,         -- ⭐⭐⭐⭐ 数量（细分）
            top_codes TEXT,                        -- JSON: [{code,name,starLevel,...}]
            total_scanned INTEGER,                 -- 本次扫了多少只
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT DEFAULT ''
        )
    ''')

    # 开启外键约束（sqlite 默认关闭）
    c.execute('PRAGMA foreign_keys = ON')
    conn.commit()
    conn.close()

def set_cache(key, data):
    """写入缓存。updated_at 自动记录为当前时间。"""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO market_cache (key, data, updated_at)
        VALUES (?, ?, ?)
    ''', (key, json.dumps(data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_cache(key, max_age_seconds=None):
    """
    读取缓存。
    - 不传 max_age_seconds：返回 (data, updated_at_str) 或 (None, None)
    - 传 max_age_seconds：超过该秒数自动判断为过期，返回 (None, None)
    """
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT data, updated_at FROM market_cache WHERE key = ?', (key,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None, None

    data = json.loads(row['data'])
    updated_at_str = row['updated_at']

    if max_age_seconds is not None:
        try:
            updated_at = datetime.fromisoformat(updated_at_str)
            if (datetime.now() - updated_at).total_seconds() > max_age_seconds:
                return None, None
        except Exception:
            return None, None

    return data, updated_at_str

def clear_cache(key=None):
    """清除缓存。传 key 只清该条，不传则清全部。"""
    conn = get_db()
    c = conn.cursor()
    if key:
        c.execute('DELETE FROM market_cache WHERE key = ?', (key,))
    else:
        c.execute('DELETE FROM market_cache')
    conn.commit()
    conn.close()

# ========= A 股交易日历 =========
# 用 chinese_calendar 判断是否交易日。它是纯 Python 离线库，不带 V8 引擎，
# 不会和 pywebview 的 Chromium/V8 冲突（akshare 因为依赖 py_mini_racer 会崩进程）。
# chinese_calendar.is_workday 基于国务院公告的法定节假日 + 调休规则，
# A 股交易日基本等同于"法定工作日"（节假日休市，调休补班日也开市）。

def is_trading_day(d):
    """
    判断某 date 是不是 A 股交易日。
    规则：
      - 周末 → 先默认非交易日
      - chinese_calendar 识别的法定节假日 → 非交易日
      - chinese_calendar 识别的调休补班日（周末但算工作日）→ 交易日
      - 失败时降级到 weekday 判断
    """
    try:
        import chinese_calendar as cc
        return cc.is_workday(d)
    except Exception:
        # 库未安装 / 日期超出支持范围（chinese_calendar 通常支持当前年份 + 下一年）
        return d.weekday() < 5


def current_trading_day(t):
    """
    把任意时刻归属到一个"交易日"。
    规则：
      - 9:30 开盘前 → 回退到上一个交易日（今天算在昨天的交易日里）
      - 开盘后 → 今天（如果今天本身不是交易日则继续回退）
      - 周末 / 节假日 / 调休不开市日 → 回退到最近的交易日
    """
    time_of_day = t.time()
    d = t.date()
    if time_of_day < dtime(9, 30):
        d -= timedelta(days=1)
    # 最多回退两周避免死循环（春节长假最长 10 天左右）
    safety = 14
    while safety > 0 and not is_trading_day(d):
        d -= timedelta(days=1)
        safety -= 1
    return d


def is_market_cache_stale(updated_at_str, trading_ttl=60, offhours_ttl=24*3600):
    """
    行情数据专用的时效判断。支持按数据源差异化 TTL。

    判定顺序：
      1) 跨交易日 → 立即失效（保证每天开盘第一次访问必然拉新）
      2) 同一交易日、交易时段内 → trading_ttl（默认 60s）
      3) 同一交易日、非交易时段 → offhours_ttl（默认 24h，主要是兜底）

    各服务按自身刷新节奏传入适当的 trading_ttl：
        - 指数/板块/市场情绪：15s
        - 连板天梯：30s
        - sparkline 分时：保留默认 60s
        - 自选持仓 quotes：10s
    """
    if not updated_at_str:
        return True
    try:
        updated_at = datetime.fromisoformat(updated_at_str)
        now = datetime.now()

        # 规则 1: 跨交易日 → 必然失效
        if current_trading_day(updated_at) != current_trading_day(now):
            return True

        # 规则 2: 同一交易日内的"盘中 → 收盘"转换
        # 缓存写于本交易日 15:00 之前，而现在已过 15:00 → 强制刷新一次拿收盘价
        # 否则 16:00 打开 app 会一直看着今天 10:00 的盘中价
        trading_day_of_cache = current_trading_day(updated_at)
        session_close = datetime.combine(trading_day_of_cache, dtime(15, 0))
        if updated_at < session_close <= now:
            return True

        # 交易时段判定走 services.market_session（单一来源），
        # 跟前端 useSmartRefresh.js 的 isMarketOpen 共用同一份边界。
        # 含集合竞价 9:15-9:25 + 撮合静默期 9:25-9:30 —— 这两段 EM
        # 接口仍返撮合价，应走 trading_ttl 才能拿到竞价行情。
        from services import market_session
        delta = (now - updated_at).total_seconds()
        if market_session.is_trading_hours(now):
            return delta > trading_ttl
        return delta > offhours_ttl
    except Exception:
        return True

init_db()

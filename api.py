"""
PyWebview 前后端桥接层。

每个对外方法都用 @api_endpoint 装饰，统一：
- 把 service 异常捕获成 {ok: False, error, code} 信封
- 把业务数据包装成 {ok: True, data: ...} 信封

新增接口的标准流程（以"机构持仓榜"为例）：
1. services/institution_service.py 写业务逻辑
2. 在本文件 import 后加:
      @api_endpoint
      def get_institution_holdings(self, params):
          return institution_service.get_xxx(params)
"""
import json
import traceback
from datetime import date
from functools import wraps

import webview

import db
from http_client import FetchError

from services import market_service
from services import sector_service
from services import sector_stocks_service
from services import kline_service
from services import limit_up_ladder_service
from services import market_sentiment_service
from services import watchlist_service
from services import portfolio_service
from services import quote_service
from services import stock_search_service
from services import sparkline_service
from services import backup_service
from services import boss_key_service
from services import hot_list_service
from services import news_service
from services import limit_pool_service
from services import license_service
from services import admin_service
from services import tdx_service
from services import update_service
from services import alert_service
from services import candidate_pool_service
import ai_service


# 老板键合法性校验：恰好 2 键组合，且必须有 1 个修饰键 + 1 个普通键
_VALID_MODIFIERS = {'ctrl', 'alt', 'shift'}

# 同义词归一：前端 / 浏览器可能送各种命名，统一成 keyboard 库认的标准名
_KEY_ALIASES = {
    # capslock / numlock 类
    'caps lock':    'capslock',
    'capital':      'capslock',
    'caps':         'capslock',
    'num lock':     'numlock',
    'scroll lock':  'scrolllock',
    'scrolllock':   'scrolllock',
    # 方向键 alias
    'arrowup':      'up',
    'arrowdown':    'down',
    'arrowleft':    'left',
    'arrowright':   'right',
    # 其他常见 alias
    'escape':       'esc',
    'return':       'enter',
    ' ':            'space',
    'spacebar':     'space',
    'pgup':         'pageup',
    'pgdn':         'pagedown',
    'page up':      'pageup',
    'page down':    'pagedown',
    'del':          'delete',
    'ins':          'insert',
    # 反引号几种写法
    'backquote':    '`',
    'grave':        '`',
}

_VALID_KEYS = set('abcdefghijklmnopqrstuvwxyz0123456789`-=[];\',./') | {
    'space', 'enter', 'tab', 'esc', 'backspace', 'insert', 'delete',
    'home', 'end', 'pageup', 'pagedown', 'up', 'down', 'left', 'right',
    # 状态切换键（注意：capslock / numlock 跟输入法可能冲突，用户自负）
    'capslock', 'numlock', 'scrolllock',
    # 数字小键盘
    'numpad0', 'numpad1', 'numpad2', 'numpad3', 'numpad4',
    'numpad5', 'numpad6', 'numpad7', 'numpad8', 'numpad9',
    'add', 'subtract', 'multiply', 'divide', 'decimal',
} | {f'f{i}' for i in range(1, 25)}  # F1-F24（高端键盘有，扩开无害）


def _normalize_key(k):
    """把传进来的键名统一成 _VALID_KEYS 里的标准写法。"""
    k = k.strip().lower()
    return _KEY_ALIASES.get(k, k)


def _validate_two_key_combo(hotkey):
    """只允许 '<修饰键>+<普通键>' 形式，严格 2 个 token。"""
    if not hotkey or not isinstance(hotkey, str):
        raise ValueError("快捷键不能为空")
    parts = [_normalize_key(p) for p in hotkey.split('+')]
    if len(parts) != 2:
        raise ValueError(f"必须是 2 键组合（如 ctrl+b），收到 {len(parts)} 键: {hotkey}")
    mod, key = parts
    if mod not in _VALID_MODIFIERS:
        raise ValueError(f"第 1 键必须是修饰键（ctrl / alt / shift），收到: {mod}")
    if key in _VALID_MODIFIERS:
        raise ValueError(f"不能两个都是修饰键，第 2 键需要字母 / 数字 / 符号: {key}")
    if key not in _VALID_KEYS:
        raise ValueError(f"不支持的按键: {key}")
    return f'{mod}+{key}'


def _build_default_filename(sections):
    """根据选中的分区生成有辨识度的备份文件名。
    watchlist+portfolio+preferences → invest_data_backup_YYYY-MM-DD.json
    只有 watchlist → watchlist_only_YYYY-MM-DD.json
    只有 portfolio → portfolio_only_YYYY-MM-DD.json
    其他组合 → sections_a_b_YYYY-MM-DD.json
    """
    today = date.today().isoformat()
    if not sections or set(sections) == set(['watchlist', 'portfolio', 'preferences']):
        return f"invest_data_backup_{today}.json"
    if sections == ['watchlist']:
        return f"watchlist_only_{today}.json"
    if sections == ['portfolio']:
        return f"portfolio_only_{today}.json"
    return f"sections_{'_'.join(sorted(sections))}_{today}.json"


# 未激活时仍可调用的方法（激活检测 / 激活码提交 / 心跳）
_LICENSE_EXEMPT = {'is_activated', 'activate_license', 'get_system_status'}


def api_endpoint(func):
    """统一 try/except + 响应信封封装 + 激活门禁。"""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # 激活门禁：未激活的所有非白名单方法直接拒绝
        if func.__name__ not in _LICENSE_EXEMPT and not license_service.is_activated():
            return {"ok": False, "error": "未激活，请先输入激活码", "code": "NOT_ACTIVATED"}
        try:
            data = func(self, *args, **kwargs)
            return {"ok": True, "data": data}
        except FetchError as e:
            return {"ok": False, "error": str(e), "code": e.code}
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "error": str(e), "code": "INTERNAL"}
    return wrapper


def admin_only(func):
    """管理员才可调用的端点装饰器。叠加在 @api_endpoint 内层。"""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not admin_service.is_admin():
            return {"ok": False, "error": "需要管理员权限", "code": "NOT_ADMIN"}
        return func(self, *args, **kwargs)
    return wrapper


class Api:
    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    @api_endpoint
    def get_system_status(self):
        return {"status": "online", "message": "Desktop Backend (Python) connected!"}

    # ============ 激活 / 授权 ============
    @api_endpoint
    def is_activated(self):
        """是否已激活。前端用此判断要不要拦在激活页。"""
        return license_service.is_activated()

    @api_endpoint
    def activate_license(self, code):
        """提交激活码。成功返回 True；签名错误返回 False。"""
        return license_service.activate(code)

    # ============ 管理员模式（基于已激活之上做权限分级）============
    _tdx_warmed = False  # 进程级标记：避免重复预热

    @api_endpoint
    def is_admin(self):
        """当前是否处于管理员模式。前端用此决定要不要显示扫描器/复杂指标 UI。
        若是管理员且本进程未预热过，顺便后台预热 pytdx — 启动后第一次打开 K 线无延迟。"""
        ok = admin_service.is_admin()
        if ok and not Api._tdx_warmed:
            Api._tdx_warmed = True
            import threading
            threading.Thread(target=tdx_service.warmup, daemon=True).start()
        return ok

    @api_endpoint
    def unlock_admin(self, password):
        """输入管理员密码升级。成功返回 True；密码错误返回 False。
        升级成功时后台预热 pytdx 连接 — 用户后续打开 K 线/扫描时连接已就绪。"""
        ok = admin_service.unlock(password)
        if ok:
            import threading
            threading.Thread(target=tdx_service.warmup, daemon=True).start()
        return ok

    @api_endpoint
    def disable_admin(self):
        """退出管理员模式（普通用户视图）。"""
        admin_service.disable()
        return True

    # ============ 在线更新 ============
    @api_endpoint
    def check_update(self):
        """联网拉 latest.json 比版本号。返回包含 has_update / download_url 等的 dict。"""
        return update_service.check_update()

    @api_endpoint
    def start_update_download(self, download_url, expected_sha256, total_bytes=0):
        """启动后台下载。前端轮询 get_update_progress() 看进度。"""
        ok = update_service.start_download(download_url, expected_sha256, total_bytes)
        return {'started': ok}

    @api_endpoint
    def get_update_progress(self):
        """轮询下载状态：phase / downloaded_bytes / total_bytes / error。"""
        return update_service.get_progress()

    @api_endpoint
    def cancel_update_download(self):
        """取消下载（清状态；正在跑的线程会自然结束）。"""
        update_service.cancel_download()
        return True

    @api_endpoint
    def apply_update(self):
        """触发更新：写 updater.bat、启动它、本进程退出。
        成功的话本调用永远不会返回（进程已退出）；失败返回 {ok:false, error}。"""
        return update_service.apply_update()

    @api_endpoint
    def get_app_version(self):
        """返回当前 app 版本号 + 数据库路径，给 Settings 页展示用。"""
        from version import __version__
        import os as _os
        return {
            'version':  __version__,
            'data_dir': _os.path.dirname(db.DB_PATH),
            'db_path':  db.DB_PATH,
        }

    @api_endpoint
    def open_data_directory(self):
        """用资源管理器打开当前数据目录，方便用户备份 / 检查数据。"""
        import os as _os
        data_dir = _os.path.dirname(db.DB_PATH)
        _os.makedirs(data_dir, exist_ok=True)
        if _os.name == 'nt':
            _os.startfile(data_dir)
        else:
            import subprocess as _sp
            _sp.Popen(['xdg-open', data_dir])
        return data_dir

    @api_endpoint
    def pick_data_directory(self):
        """弹原生"选择文件夹"对话框，返回 {cancelled, path}。"""
        if not self._window:
            raise RuntimeError("pywebview 窗口未就绪")
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return {"cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        return {"cancelled": False, "path": path}

    @api_endpoint
    def change_data_directory(self, new_path, migrate=True):
        """
        切换数据目录。
        Args:
            new_path: 新路径（必须可写）
            migrate:  True 时把当前 invest_data.db 拷一份到新目录
        Returns:
            {"new_path": ..., "migrated": bool, "restart_required": True}
        副作用：修改 %APPDATA%\\InvestTool\\config.json；下次启动 db.py 读取新路径生效。
        """
        import os as _os
        import shutil as _shutil
        import app_config

        new_path = _os.path.abspath(new_path)
        _os.makedirs(new_path, exist_ok=True)

        # 可写性测试
        try:
            test = _os.path.join(new_path, '.write_test')
            with open(test, 'w', encoding='utf-8') as f:
                f.write('ok')
            _os.remove(test)
        except (OSError, PermissionError) as e:
            raise RuntimeError(f"目标目录不可写：{e}")

        # 迁移现有 db
        migrated = False
        current_db = db.DB_PATH
        new_db = _os.path.join(new_path, 'invest_data.db')
        same = _os.path.exists(new_db) and _os.path.abspath(current_db) == _os.path.abspath(new_db)
        if migrate and _os.path.exists(current_db) and not same:
            _shutil.copy2(current_db, new_db)
            migrated = True

        # 写 config
        app_config.set_data_dir(new_path)

        return {
            "new_path": new_path,
            "migrated": migrated,
            "restart_required": True,
        }

    @api_endpoint
    def reset_data_directory(self):
        """恢复默认数据目录（%APPDATA%\\InvestTool\\）。下次启动生效。"""
        import app_config
        app_config.reset_data_dir()
        return {"restart_required": True}

    @api_endpoint
    def restart_app(self):
        """退出当前进程；用户需手动重启（或可调用方在前端用 updater 类似手段拉起）。"""
        import threading as _t
        _t.Timer(0.2, lambda: __import__('os')._exit(0)).start()
        return True

    @api_endpoint
    def get_market_data(self, date=None):
        """
        一次性返回 Market 页概览数据。
        date='YYYY-MM-DD' 历史模式：indices 返回 None（前端隐藏），sectors 走 KPL 历史接口。
        """
        overview = market_service.get_market_indices(date=date)
        sectors = sector_service.get_hot_sectors(date=date)
        return {
            "indices": (overview or {}).get("indices") if overview else None,
            "total_turnover": (overview or {}).get("total_turnover", 0) if overview else 0,
            "hotSectors": sectors,
        }

    @api_endpoint
    def get_kline(self, name, timeframe):
        """指数 K 线（按显示名查找）。"""
        return kline_service.get_kline(name, timeframe)

    @api_endpoint
    def get_stock_kline(self, code, timeframe):
        """个股 K 线（任意 6 位代码）。timeframe: 分时/5日/日K/周K/月K/年K"""
        return kline_service.get_stock_kline(code, timeframe)

    @api_endpoint
    @admin_only
    def get_stock_kline_via_tdx(self, code, timeframe='日K', count=800):
        """
        管理员专用：严格 TDX-only。
        TDX 拉不到（北交所 / 服务器全挂 / 单股票数据缺失）→ 直接返回 []，
        前端表现为"数据不足"。绝不 fallback 到腾讯/EM，避免触发反爬。
        count 超过 800 自动分页，最多可拉至股票上市日全历史。
        """
        return tdx_service.get_stock_kline(code, timeframe, count=count)

    @api_endpoint
    @admin_only
    def get_index_kline_via_tdx(self, name, timeframe='日K', count=300):
        """
        管理员专用：指数 K 线走 TDX（沪深 300 / 上证指数 等）。
        给 useMarketEnv 用，避免大盘环境也打腾讯。
        """
        return tdx_service.get_index_kline(name, timeframe, count=count)

    @api_endpoint
    @admin_only
    def tdx_is_available(self):
        """TDX 连接是否可用（管理员 UI 上可显示状态）。"""
        return tdx_service.is_available()

    @api_endpoint
    @admin_only
    def list_all_a_share_codes(self):
        """全市场 A 股代码列表（admin only），给批量回测 / 全市场扫描用。约 5000 条。"""
        return tdx_service.get_all_a_share_codes()

    @api_endpoint
    @admin_only
    def get_stock_kline_via_tdx_cached(self, code, timeframe='日K', count=800):
        """带 SQLite 持久化缓存的 TDX K线（仅回测用，每日刷新，跨重启留存）。"""
        return tdx_service.get_stock_kline_cached(code, timeframe, count)

    @api_endpoint
    @admin_only
    def kline_cache_stats(self):
        """K线缓存统计：行数 / 当日命中数 / 文件大小。"""
        from services import kline_cache_service
        return kline_cache_service.stats()

    @api_endpoint
    @admin_only
    def kline_cache_clear(self):
        """清空 K线缓存表 + VACUUM。"""
        from services import kline_cache_service
        kline_cache_service.clear()
        return {'cleared': True}

    @api_endpoint
    @admin_only
    def bulk_check_kline_freshness(self, codes, timeframe='日K'):
        """
        批量查询缓存里每只票 K 线最后一根日期，得出哪些已有今日数据 / 哪些待下载。
        用于"下载今日 K 线"按钮的预检：避免无脑全量重下，只下缺口。
        """
        from services import kline_cache_service
        return kline_cache_service.bulk_check_freshness(codes, timeframe)

    @api_endpoint
    def get_sector_stocks(self, plate_id, date=None):
        """根据 KPL 板块 ID 返回该板块精选联动股票列表。date 历史日期 'YYYY-MM-DD'。"""
        return sector_stocks_service.get_sector_stocks(plate_id, date=date)

    @api_endpoint
    def get_all_sectors(self, limit=80):
        """全量板块榜（前 N 个，默认 80），给热力图用。"""
        return sector_service.get_all_sectors(limit=limit)

    @api_endpoint
    def get_limit_up_ladder(self, date=None):
        """连板天梯：按连板高度降序分组。后端返全量（含 ST + 次新），前端按需筛选。"""
        return limit_up_ladder_service.get_ladder(date=date)

    @api_endpoint
    def get_market_sentiment(self, date=None):
        """市场情绪：成交额 + 涨跌家数 + 涨停。date 传了直接返回 None（历史不支持）。"""
        return market_sentiment_service.get_sentiment(date=date)

    @api_endpoint
    def get_ths_hot_list(self, period='hour'):
        """同花顺热榜。period: 'hour' (1 小时) | 'day' (24 小时)"""
        return hot_list_service.get_hot_list(period=period)

    @api_endpoint
    def get_fast_news(self, source='ths'):
        """快讯。source: 'ths' (同花顺) | 'em' (东方财富)"""
        return news_service.get_fast_news(source=source)

    @api_endpoint
    def get_limit_pools(self, date=None):
        """涨跌池聚合：连板/涨停/炸板/冲刺涨停/跌停 五个 THS 池子，固定顺序。"""
        return limit_pool_service.get_pools(date=date)

    @api_endpoint
    def get_limit_pool(self, pool_key, date=None):
        """单池查询（前端按需加载）。pool_key: continuous/limitUp/broken/sprint/limitDown"""
        return limit_pool_service.get_pool(pool_key, date=date)

    @api_endpoint
    def get_trading_days(self, start_date=None, end_date=None):
        """
        返回 [start, end] 区间内所有 A 股交易日，'YYYY-MM-DD' 字符串列表。
        默认 end=今天，start=今天往前 2 年（够日期选择器 disabled 判定用了）。
        """
        from datetime import date as _date, timedelta
        end = _date.fromisoformat(end_date) if end_date else _date.today()
        start = _date.fromisoformat(start_date) if start_date else (end - timedelta(days=730))
        days = []
        cur = start
        while cur <= end:
            if db.is_trading_day(cur):
                days.append(cur.isoformat())
            cur += timedelta(days=1)
        return days

    # =========== 自选股 Watchlist =========== #

    @api_endpoint
    def get_watchlist_groups(self):
        """所有自选分组（按 sort_order 升序）"""
        return watchlist_service.get_groups()

    @api_endpoint
    def create_watchlist_group(self, name):
        """创建新分组，返回 {id, name}"""
        gid = watchlist_service.create_group(name)
        return {"id": gid, "name": name}

    @api_endpoint
    def rename_watchlist_group(self, group_id, new_name):
        watchlist_service.rename_group(group_id, new_name)
        return {"ok": True}

    @api_endpoint
    def delete_watchlist_group(self, group_id):
        watchlist_service.delete_group(group_id)
        return {"ok": True}

    @api_endpoint
    def reorder_watchlist_groups(self, ordered_ids):
        """参数是 group id 数组，顺序即新排序"""
        watchlist_service.reorder_groups(ordered_ids)
        return {"ok": True}

    @api_endpoint
    def get_watchlist_stocks(self, group_id):
        """某分组下的股票列表（不含实时行情，P2 另行填充）"""
        return watchlist_service.get_group_stocks(group_id)

    @api_endpoint
    def get_all_watchlist_stocks(self):
        """虚拟分组'全部自选'：所有分组股票合并去重，返回含 groups 字段"""
        return watchlist_service.get_all_stocks_deduped()

    @api_endpoint
    def add_watchlist_stock(self, group_id, code, name='', added_price=None, remark=''):
        watchlist_service.add_stock(group_id, code, name, added_price, remark)
        return {"ok": True}

    @api_endpoint
    def update_watchlist_stock(self, group_id, code, name=None, added_price=None, remark=None, added_at=None):
        watchlist_service.update_stock(group_id, code, name=name, added_price=added_price,
                                        remark=remark, added_at=added_at)
        return {"ok": True}

    # ---------- 批量导入（文本 / 图片）----------
    @api_endpoint
    def import_parse_text(self, text):
        """
        从一段文本里识别股票代码 + 名称。
        返回 [{code, name, source}, ...]，前端预览后再调 import_batch_add 入库。
        """
        from services import import_service
        return import_service.parse_text(text or '')

    @api_endpoint
    def import_batch_add(self, group_id, stocks):
        """
        批量加入分组。已存在的 code 跳过。
        Args:
            group_id: int
            stocks: [{code, name}]
        Returns:
            {added, skipped_existing, failed, detail}
        """
        from services import import_service
        return import_service.batch_add_to_group(group_id, stocks or [])

    # ---------- 价格警报 ----------
    @api_endpoint
    def set_stock_alert(self, code, above=None, below=None):
        """设警报。above/below 至少传一个；都传 None 等同清除。"""
        return alert_service.set_alert(code, above=above, below=below)

    @api_endpoint
    def clear_stock_alert(self, code):
        """清除某只股的警报。"""
        return alert_service.clear_alert(code)

    @api_endpoint
    def get_stock_alert(self, code):
        """读单只股的当前警报阈值。返回 None / {alert_above, alert_below}"""
        return alert_service.get_alert(code)

    @api_endpoint
    def get_pending_alerts(self, limit=20):
        """前端轮询：获取未展示过的触发记录（按时间倒序）"""
        return alert_service.get_pending_alerts(limit=limit)

    @api_endpoint
    def ack_alerts(self, ids):
        """前端展示完成后调，标记为已展示。"""
        return alert_service.ack_alerts(ids or [])

    @api_endpoint
    def force_check_alerts(self):
        """立即检查所有警报（绕过 scheduler 节奏）。返回本次触发数。"""
        return alert_service.check_alerts()

    @api_endpoint
    def search_stocks(self, query, limit=20):
        """股票搜索：代码/中文名/拼音缩写，仅返 A 股"""
        return stock_search_service.search_stocks(query, limit)

    @api_endpoint
    def remove_watchlist_stock(self, group_id, code):
        watchlist_service.remove_stock(group_id, code)
        return {"ok": True}

    @api_endpoint
    def remove_watchlist_stocks_batch(self, group_id, codes):
        """批量从分组移除股票。失败的不影响其它继续删。"""
        codes = codes or []
        removed = 0
        failed = []
        for code in codes:
            try:
                watchlist_service.remove_stock(group_id, code)
                removed += 1
            except Exception as e:
                failed.append({'code': code, 'error': str(e)})
        return {'removed': removed, 'failed': failed}

    @api_endpoint
    def reorder_watchlist_stocks(self, group_id, ordered_codes):
        watchlist_service.reorder_stocks(group_id, ordered_codes)
        return {"ok": True}

    @api_endpoint
    def get_user_preference(self, key, default=None):
        return watchlist_service.get_preference(key, default)

    @api_endpoint
    def set_user_preference(self, key, value):
        watchlist_service.set_preference(key, value)
        return {"ok": True}

    # =========== 持仓 Portfolio =========== #

    @api_endpoint
    def get_portfolio_accounts(self):
        """所有持仓账户（按 sort_order）"""
        return portfolio_service.get_accounts()

    @api_endpoint
    def create_portfolio_account(self, name):
        aid = portfolio_service.create_account(name)
        return {"id": aid, "name": name}

    @api_endpoint
    def rename_portfolio_account(self, account_id, new_name):
        portfolio_service.rename_account(account_id, new_name)
        return {"ok": True}

    @api_endpoint
    def delete_portfolio_account(self, account_id):
        portfolio_service.delete_account(account_id)
        return {"ok": True}

    @api_endpoint
    def reorder_portfolio_accounts(self, ordered_ids):
        portfolio_service.reorder_accounts(ordered_ids)
        return {"ok": True}

    @api_endpoint
    def get_portfolio_positions(self, account_id):
        """某账户下的持仓列表（不含实时行情，前端另拉 quotes 合并）"""
        return portfolio_service.get_account_positions(account_id)

    @api_endpoint
    def add_portfolio_position(self, account_id, code, name='', shares=None, cost_price=None, remark=''):
        portfolio_service.add_position(account_id, code, name, shares, cost_price, remark)
        return {"ok": True}

    @api_endpoint
    def update_portfolio_position(self, account_id, code, name=None, shares=None,
                                  cost_price=None, remark=None, added_at=None):
        portfolio_service.update_position(account_id, code, name=name, shares=shares,
                                          cost_price=cost_price, remark=remark, added_at=added_at)
        return {"ok": True}

    @api_endpoint
    def remove_portfolio_position(self, account_id, code):
        portfolio_service.remove_position(account_id, code)
        return {"ok": True}

    @api_endpoint
    def reorder_portfolio_positions(self, account_id, ordered_codes):
        portfolio_service.reorder_positions(account_id, ordered_codes)
        return {"ok": True}

    @api_endpoint
    def get_portfolio_merged(self):
        """'汇总'虚拟账户：所有账户持仓按 code 合并（Step 3 用）"""
        return portfolio_service.get_all_positions_merged()

    # =========== 数据备份 Backup =========== #

    @api_endpoint
    def export_user_data(self):
        """导出自选 + 持仓 + 偏好为 JSON 可序列化 dict。前端触发文件下载。"""
        return backup_service.export_user_data()

    @api_endpoint
    def import_user_data(self, data, mode='replace'):
        """从 dict 恢复用户数据。返回每张表的写入条数。"""
        counts = backup_service.import_user_data(data, mode=mode)
        return {"imported": counts}

    @api_endpoint
    def export_user_data_interactive(self, sections=None):
        """
        打开原生"另存为"对话框写入 JSON 文件。
        sections: 'watchlist'/'portfolio'/'preferences' 的子集；None = 全部
        返回 {cancelled: True} 或 {cancelled: False, path, counts, sections}。
        """
        if not self._window:
            raise RuntimeError("pywebview 窗口未就绪")
        # 根据分区组合定制默认文件名，便于一眼分辨共享的是啥
        default_name = _build_default_filename(sections)
        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=default_name,
            file_types=('JSON Files (*.json)',),
        )
        if not result:
            return {"cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        counts = backup_service.write_backup_to_file(path, sections=sections)
        actual_sections = sections or list(backup_service.ALL_SECTIONS)
        return {"cancelled": False, "path": path, "counts": counts, "sections": actual_sections}

    @api_endpoint
    def pick_backup_file(self):
        """
        打开文件选择对话框并返回路径 + 预览信息。
        返回 {cancelled: True} 或 {cancelled: False, path, schema_version, exported_at, counts}。
        """
        if not self._window:
            raise RuntimeError("pywebview 窗口未就绪")
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=('JSON Files (*.json)',),
            allow_multiple=False,
        )
        if not result:
            return {"cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        data = backup_service.read_backup_from_file(path)
        counts = {t: len(data.get(t) or []) for t in backup_service.USER_TABLES}
        return {
            "cancelled": False,
            "path": path,
            "schema_version": data.get("schema_version"),
            "exported_at": data.get("exported_at"),
            "counts": counts,
        }

    @api_endpoint
    def import_backup_file(self, path, mode='replace'):
        """从指定路径导入备份文件。"""
        counts = backup_service.import_from_file(path, mode=mode)
        return {"imported": counts}

    # =========== 候选池（找发车 / 找候选 收藏后持续追踪买点）=========== #

    @api_endpoint
    def list_candidate_picks(self):
        """列出全部候选池条目，按入选时间倒序。前端配合实时行情算当前状态。"""
        return candidate_pool_service.list_picks()

    @api_endpoint
    def add_candidate_pick(self, payload):
        """
        把扫描器选中的票存入候选池。
        payload 字段：code, name, stage, save_price, break_level, golden_price,
                     s1_lower (可空), consolidation_bars (可空), source, note
        同 code 重复添加会覆盖旧 snapshot。
        """
        if not isinstance(payload, dict):
            raise ValueError('payload 必须是 dict')
        return candidate_pool_service.add_pick(
            code=payload.get('code'),
            name=payload.get('name'),
            stage=payload.get('stage'),
            save_price=payload.get('save_price'),
            break_level=payload.get('break_level'),
            golden_price=payload.get('golden_price'),
            s1_lower=payload.get('s1_lower'),
            consolidation_bars=payload.get('consolidation_bars'),
            source=payload.get('source') or '三维启动找候选',
            note=payload.get('note') or '',
        )

    @api_endpoint
    def remove_candidate_pick(self, code):
        """从候选池删一只票。"""
        return candidate_pool_service.remove_pick(code)

    @api_endpoint
    def update_candidate_note(self, code, note):
        """改备注。"""
        return candidate_pool_service.update_note(code, note)

    @api_endpoint
    def clear_candidate_picks(self):
        """清空整个候选池（危险操作，前端要二次确认）。"""
        return candidate_pool_service.clear_all()

    @api_endpoint
    def update_candidate_tracking(self, payload):
        """
        Phase 5：更新单条候选池的动态追踪字段（peak_gain_since_save / formation_state /
        last_refreshed_at）。前端跑完 detector 后调用。
        """
        if not isinstance(payload, dict):
            raise ValueError('payload 必须是 dict')
        return candidate_pool_service.update_tracking(
            code=payload.get('code'),
            peak_gain_since_save=payload.get('peak_gain_since_save'),
            formation_state=payload.get('formation_state'),
        )

    @api_endpoint
    def bulk_update_candidate_tracking(self, payloads):
        """
        Phase 5：批量更新追踪字段（候选池刷新一次性写回所有票）。
        payloads: [{code, peak_gain_since_save?, formation_state?}, ...]
        所有记录共用同一个 last_refreshed_at。
        """
        if not isinstance(payloads, list):
            raise ValueError('payloads 必须是 list')
        return candidate_pool_service.bulk_update_tracking(payloads)

    # =========== 老板键 =========== #

    @api_endpoint
    def get_boss_key(self):
        """当前已注册的老板键（hotkey 字符串，可能是 None 表示没注册）"""
        return {"hotkey": boss_key_service.get_current()}

    @api_endpoint
    def set_boss_key(self, hotkey):
        """修改老板键。严格 2 键组合，成功后立即热切换，并持久化到 user_preferences。"""
        normalized = _validate_two_key_combo(hotkey)  # 校验 + 标准化大小写
        boss_key_service.update(normalized)            # 运行时热切换
        watchlist_service.set_preference('boss_key', normalized)  # 持久化
        return {"hotkey": normalized}

    @api_endpoint
    def get_batch_quotes(self, codes):
        """按代码批量查实时行情 + 基本面（自选列表用）"""
        return quote_service.get_batch_quotes(codes or [])

    @api_endpoint
    def get_batch_sparklines(self, codes):
        """
        按代码批量查当日分时（画 sparkline 用）。返回 {code: {preClose, prices, avgPrices}}。
        统一走 EM trends2 — pytdx 批量分时有响应缓冲污染问题，不可靠。
        """
        return sparkline_service.get_batch_sparklines(codes or [])

    @api_endpoint
    def analyze_market_query(self, query):
        overview = market_service.get_market_indices()
        sectors = sector_service.get_hot_sectors()
        context = json.dumps({
            "indices": overview.get("indices", []),
            "top_hot_sectors": sectors[:3],
        }, ensure_ascii=False)
        return ai_service.analyze_query(query, context=context)

    @api_endpoint
    def refresh_cache(self):
        """强制清除所有缓存并重拉行情概览。"""
        db.clear_cache()
        overview = market_service.get_market_indices(force=True)
        sectors = sector_service.get_hot_sectors(force=True)
        return {
            "indices": overview.get("indices", []),
            "total_turnover": overview.get("total_turnover", 0),
            "hotSectors": sectors,
        }

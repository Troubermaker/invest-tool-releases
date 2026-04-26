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
import ai_service


# 老板键合法性校验：恰好 2 键组合，且必须有 1 个修饰键 + 1 个普通键
_VALID_MODIFIERS = {'ctrl', 'alt', 'shift'}
_VALID_KEYS = set('abcdefghijklmnopqrstuvwxyz0123456789`-=[];\',./') | {
    'space', 'enter', 'tab', 'esc', 'backspace', 'insert', 'delete',
    'home', 'end', 'pageup', 'pagedown', 'up', 'down', 'left', 'right',
} | {f'f{i}' for i in range(1, 13)}  # F1-F12


def _validate_two_key_combo(hotkey):
    """只允许 '<修饰键>+<普通键>' 形式，严格 2 个 token。"""
    if not hotkey or not isinstance(hotkey, str):
        raise ValueError("快捷键不能为空")
    parts = [p.strip().lower() for p in hotkey.split('+')]
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


def api_endpoint(func):
    """统一 try/except + 响应信封封装。"""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            data = func(self, *args, **kwargs)
            return {"ok": True, "data": data}
        except FetchError as e:
            return {"ok": False, "error": str(e), "code": e.code}
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "error": str(e), "code": "INTERNAL"}
    return wrapper


class Api:
    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    @api_endpoint
    def get_system_status(self):
        return {"status": "online", "message": "Desktop Backend (Python) connected!"}

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
        return kline_service.get_kline(name, timeframe)

    @api_endpoint
    def get_sector_stocks(self, plate_id, date=None):
        """根据 KPL 板块 ID 返回该板块精选联动股票列表。date 历史日期 'YYYY-MM-DD'。"""
        return sector_stocks_service.get_sector_stocks(plate_id, date=date)

    @api_endpoint
    def get_limit_up_ladder(self, date=None):
        """连板天梯：按连板高度降序分组的股票列表。date 历史日期 'YYYY-MM-DD'。"""
        return limit_up_ladder_service.get_ladder(date=date)

    @api_endpoint
    def get_market_sentiment(self, date=None):
        """市场情绪：成交额 + 涨跌家数 + 涨停。date 传了直接返回 None（历史不支持）。"""
        return market_sentiment_service.get_sentiment(date=date)

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

    @api_endpoint
    def search_stocks(self, query, limit=20):
        """股票搜索：代码/中文名/拼音缩写，仅返 A 股"""
        return stock_search_service.search_stocks(query, limit)

    @api_endpoint
    def remove_watchlist_stock(self, group_id, code):
        watchlist_service.remove_stock(group_id, code)
        return {"ok": True}

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
            webview.SAVE_DIALOG,
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
            webview.OPEN_DIALOG,
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
        """按代码批量查当日分时（画 sparkline 用）。返回 {code: {preClose, prices}}"""
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

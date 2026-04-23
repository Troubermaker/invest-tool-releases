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
from functools import wraps

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
import ai_service


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
    def get_market_data(self):
        """一次性返回首页需要的概览数据。"""
        overview = market_service.get_market_indices()
        sectors = sector_service.get_hot_sectors()
        return {
            "indices": overview.get("indices", []),
            "total_turnover": overview.get("total_turnover", 0),
            "hotSectors": sectors,
        }

    @api_endpoint
    def get_kline(self, name, timeframe):
        return kline_service.get_kline(name, timeframe)

    @api_endpoint
    def get_sector_stocks(self, plate_id):
        """根据 KPL 板块 ID 返回该板块精选联动股票列表"""
        return sector_stocks_service.get_sector_stocks(plate_id)

    @api_endpoint
    def get_limit_up_ladder(self):
        """连板天梯：按连板高度降序分组的股票列表"""
        return limit_up_ladder_service.get_ladder()

    @api_endpoint
    def get_market_sentiment(self):
        """市场情绪：全市场成交额（较昨日）+ 涨跌家数 + 涨停跌停"""
        return market_sentiment_service.get_sentiment()

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

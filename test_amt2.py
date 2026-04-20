import sys; sys.path.insert(0, '.')
import importlib, fetcher
importlib.reload(fetcher)

for name in ['深证成指', '创业板指', '沪深300', '科创50']:
    data = fetcher.get_kline_data(name, '日K')
    if data:
        last = data[-1]
        print(f"{name}: 收={last['close']:.2f}  成交额={last.get('amt'):.2f}亿  成交量={last.get('vol'):.2f}万手")
    else:
        print(f"{name}: 空数据")

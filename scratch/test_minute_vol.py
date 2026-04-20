import sys; sys.path.insert(0, '.')
import fetcher

name = '上证指数'
data = fetcher.get_kline_data(name, '分时')
if data:
    last = data[-1]
    # In the intraday data, value is used for price, and amt is cumulative-to-minute delta
    print(f"{name} 分时末行:")
    print(f"  均价={last['value']:.2f}")
    if 'vol' in last: print(f"  成交量(分时单位)={last['vol']:.2f}万手")
    if 'amt' in last: print(f"  成交额(分时单位)={last['amt'] if last['amt'] else 0:.4f}亿")
else:
    print(f"{name} 分时数据为空")

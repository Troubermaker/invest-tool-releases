import requests

# 测试腾讯的 newfqkline 接口（可能包含成交额）
r = requests.get('http://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sz399001,day,,,3,qfq', timeout=5)
import json
d = r.json()
arr = d.get('data',{}).get('sz399001',{})
key = list(arr.keys())[0] if arr else None
print('newfqkline key:', key)
if key:
    print('前3行:', arr[key][:3])

# 测试东方财富用不同UA
urls_to_test = [
    ('sz399001', '0.399001'),  # 深证成指
    ('sz399006', '0.399006'),  # 创业板指
    ('sh000300', '1.000300'),  # 沪深300
]
for sym, secid in urls_to_test:
    try:
        res = requests.get(
            f'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58&klt=101&fqt=0&beg=20260417&end=20260420',
            headers={
                'Referer': 'https://quote.eastmoney.com/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
            }, 
            timeout=5
        ).json()
        klines = res.get('data', {}).get('klines') or []
        print(f'{sym}: EM数据 {len(klines)} 行, 样例:', klines[:1] if klines else '空')
    except Exception as e:
        print(f'{sym}: EM异常-', type(e).__name__)

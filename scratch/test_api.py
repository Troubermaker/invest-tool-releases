import requests
import json

def test_sina_indices():
    target_indices = [
        ('上证指数', 's_sh000001'),
        ('深证成指', 's_sz399001'),
    ]
    symbols_str = ",".join([sym for _, sym in target_indices])
    url = f"http://hq.sinajs.cn/list={symbols_str}"
    headers = {'Referer': 'http://finance.sina.com.cn'}
    res = requests.get(url, headers=headers, timeout=5)
    res.encoding = 'gbk'
    print("Sina Response:")
    print(res.text)

def test_tencent_minute():
    symbol = 'sh000001'
    url = f"http://ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
    res = requests.get(url, timeout=5).json()
    # print(json.dumps(res, indent=2, ensure_ascii=False))
    qt = res['data'][symbol]['qt'][symbol]
    print(f"\nTencent QT for {symbol}:")
    print(qt)
    # The date is usually in qt[1] or similar
    # Indices in Tencent QT:
    # 0: name
    # 1: code
    # 2: current
    # 3: settlement (yesterday close)
    # 4: open
    # ...
    # 30: time (YYYYMMDDHHMMSS)

if __name__ == "__main__":
    test_sina_indices()
    test_tencent_minute()

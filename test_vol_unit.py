import requests

vol_raw = 548791406
amt_yuan = 1013520660231

avg_shou = amt_yuan / vol_raw
avg_gu = avg_shou / 100
print("EM raw:", vol_raw)
print("if shou: avg_price/shou=%.2f, avg/gu=%.2f" % (avg_shou, avg_gu))
reasonable = "OK" if 5 < avg_gu < 100 else "WRONG"
print("unit=shou ->", reasonable)

# Sina 对比
r = requests.get('http://hq.sinajs.cn/list=s_sh000001',
    headers={'Referer':'http://finance.sina.com.cn'}, timeout=5)
r.encoding = 'gbk'
parts = r.text.split('"')[1].split(',')
sina_vol = float(parts[3])
print("Sina vol:", sina_vol, "  ratio EM/Sina:", vol_raw/sina_vol)

# Tencent newfqkline 深证
r2 = requests.get('http://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sz399001,day,,,1,qfq').json()
row = r2['data']['sz399001']['day'][-1]
vol2 = float(row[5])
amt2_yuan = float(row[8]) * 10000
avg2 = amt2_yuan / vol2
print("\nTencent newfqkline sz399001:")
print("  raw vol=%.0f" % vol2)
print("  if shou: avg/gu=%.2f" % (avg2/100))

r3 = requests.get('http://hq.sinajs.cn/list=s_sz399001',
    headers={'Referer':'http://finance.sina.com.cn'}, timeout=5)
r3.encoding = 'gbk'
p3 = r3.text.split('"')[1].split(',')
sina_vol2 = float(p3[3])
print("  Sina vol=%.0f  ratio=%.4f" % (sina_vol2, vol2/sina_vol2))

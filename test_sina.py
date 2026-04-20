import requests

r = requests.get('http://hq.sinajs.cn/list=sh000001,sz399001,sz399006',
    headers={'Referer':'http://finance.sina.com.cn'}, timeout=5)
r.encoding = 'gbk'
lines = r.text.strip().split('\n')
for line in lines:
    val = line.split('=')[1].strip('\";\n\r')
    parts = val.split(',')
    print(f'名称:{parts[0]} | 昨收:{parts[2]} | 今开:{parts[1]} | 成交量:{parts[8]}手 | 成交额:{parts[9]}元 | 共{len(parts)}个字段')

import akshare as ak
import pandas as pd
import db
import requests
import time
import datetime
from datetime import datetime, time as dtime

def is_cache_stale(updated_at_str):
    """判断缓存是否过期"""
    if not updated_at_str:
        return True
    try:
        updated_at = datetime.fromisoformat(updated_at_str)
        now = datetime.now()
        
        # 1. 跨天一定失效
        if updated_at.date() < now.date():
            return True
            
        # 2. 交易时段 (9:30-11:35, 13:00-15:05) 超过 1 分钟失效
        cur = now.time()
        is_trading = (dtime(9, 30) <= cur <= dtime(11, 35)) or \
                     (dtime(13, 0) <= cur <= dtime(15, 0))
        
        delta = (now - updated_at).total_seconds()
        if is_trading:
            return delta > 60
        else:
            # 盘后或周末，1小时刷新一次
            return delta > 3600
    except Exception:
        return True

def get_market_indices(force=False):
    """获取顶部的核心指数数据并缓存"""
    cached, updated_at = db.get_cache('market_indices')
    if not force and cached and not is_cache_stale(updated_at):
        return cached

    # Map Sina symbols to what user requested
    # s_sh000001 = 上证指数, s_sz399001 = 深证成指, s_sz399006 = 创业板指
    # s_sh000688 = 科创50, s_bj899050 = 北证50, s_sh000300 = 沪深300
    # s_sh000905 = 中证500, s_sh000852 = 中证1000
    
    target_indices = [
        ('上证指数', 's_sh000001'),
        ('深证成指', 's_sz399001'),
        ('创业板指', 's_sz399006'),
        ('科创50', 's_sh000688'),
        ('北证50', 's_bj899050'),
        ('沪深300', 's_sh000300'),
        ('中证500', 's_sh000905'),
        ('中证1000', 's_sh000852'),
    ]

    try:
        symbols_str = ",".join([sym for _, sym in target_indices])
        url = f"http://hq.sinajs.cn/list={symbols_str}"
        headers = {'Referer': 'http://finance.sina.com.cn'}
        
        # Use a quick requests call which is 10x faster and immune to AkShare EM connection resets
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = 'gbk' # Sina returns GBK
        lines = res.text.strip().split('\n')
        
        results = []
        total_amt = 0.0
        for i, line in enumerate(lines):
            if not line: continue
            # Output format: var hq_str_s_sh000001="上证指数,4055.55,28.34,0.70,5458769,97656549";
            data_str = line.split('=')[1].strip('";')
            cols = data_str.split(',')
            if len(cols) >= 6:
                name = target_indices[i][0]
                price = f"{float(cols[1]):.2f}"
                change_val = f"{float(cols[2]):.2f}"
                change_pct = f"{float(cols[3]):.2f}"
                up = float(cols[3]) >= 0
                
                # cols[5] 为成交额 (万元)
                idx_amt = float(cols[5]) / 10000 # 转为 亿
                # 仅累加上证(sh000001)和深证(sz399001)作为全市场概览
                if target_indices[i][1] in ['s_sh000001', 's_sz399001']:
                    total_amt += idx_amt

                results.append({
                    "name": name,
                    "price": price,
                    "change": f"{'+' if up else ''}{change_pct}%",
                    "value": f"{'+' if up else ''}{change_val}",
                    "up": up,
                    "amt": idx_amt
                })
        
        if not results:
            raise ValueError("No Sina index data found.")
            
        final_data = {
            "indices": results,
            "total_turnover": total_amt
        }
        db.set_cache('market_indices', final_data)
        return final_data
    except Exception as e:
        print(f"Error fetching indices via Sina: {e}")
        return {"indices": [], "total_turnover": 0.0}


import json

def get_hot_sectors(force=False):
    """获取精选热门板块趋势 - 切换为最底层的稳定 Sina API 防止切断连线"""
    cached, updated_at = db.get_cache('hot_sectors')
    if not force and cached and not is_cache_stale(updated_at):
        return cached
        
    try:
        url = "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
        res = requests.get(url, timeout=5)
        res.encoding = 'gbk' # Sina returns GBK
        
        # Output is var S_Finance_bankuai_sinaindustry = { ... }
        raw_json_str = res.text.split('=', 1)[1].strip().strip(';')
        data_dict = json.loads(raw_json_str)
        
        items = list(data_dict.values())
        # The 6th column (index 5) is the change percentage
        # Sort descending by change percentage
        items.sort(key=lambda x: float(x.split(',')[5]), reverse=True)
        
        results = []
        for i, rowstr in enumerate(items[:15]): # Get top 15 hot sectors
            cols = rowstr.split(',')
            if len(cols) < 8: continue
            
            name = cols[1]
            change_pct = float(cols[5])
            turnover_amount = float(cols[7]) / 100000000 # Convert to Hundred Million 亿
            
            up = change_pct >= 0
            results.append({
                "rank": i + 1,
                "name": name,
                "change": f"{'+' if up else ''}{change_pct:.2f}%",
                "inflow": f"{turnover_amount:.2f}亿", 
                "code": cols[0]
            })
            
        db.set_cache('hot_sectors', results)
        return results
    except Exception as e:
        print(f"Error fetching hot sectors via Sina: {e}")
        return []

import datetime

def get_kline_data(index_name, timeframe):
    """
    获取大盘指数的历史 K 线或分时数据。
    timeframe 支持：'分时', '5日', '日K', '周K', '月K', '年K'
    """
    target_indices = {
        '上证指数': 'sh000001', '深证成指': 'sz399001', '创业板指': 'sz399006',
        '科创50': 'sh000688', '北证50': 'bj899050', '沪深300': 'sh000300',
        '中证500': 'sh000905', '中证1000': 'sh000852',
    }
    symbol = target_indices.get(index_name, 'sh000001')
    results = []
    
    try:
        if timeframe == '分时':
            # 腾讯分时 API
            url = f"http://ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
            res = requests.get(url, timeout=5).json()
            data_arr = res['data'][symbol]['data']['data']
            qt_arr = res['data'][symbol]['qt'][symbol]
            prev_close = float(qt_arr[4])
            
            today = datetime.date.today().strftime("%Y-%m-%d")
            
            prev_cum_vol = 0
            prev_cum_amt = 0
            for line in data_arr:
                # 沪深: "0930 4043.38 5351200 9548685548.50" (4列，含成交额)
                # 北证: "0930 1341.72 60232" (3列，无成交额)
                parts = line.split(' ')
                if len(parts) < 2:
                    continue
                time_str = parts[0]
                price = float(parts[1])
                
                cum_vol = float(parts[2]) / 10000 if len(parts) > 2 else 0  # 万手
                cum_amt = float(parts[3]) / 100000000 if len(parts) > 3 else 0  # 亿，北证无此字段
                
                vol = cum_vol - prev_cum_vol if prev_cum_vol else cum_vol
                amt = cum_amt - prev_cum_amt if prev_cum_amt else cum_amt
                
                prev_cum_vol = cum_vol
                prev_cum_amt = cum_amt
                
                dt_str = f"{today} {time_str[0:2]}:{time_str[2:4]}:00+0800"
                dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S%z")
                
                chg = price - prev_close
                pct = (chg / prev_close) * 100 if prev_close else 0
                
                results.append({ 
                    "time": int(dt.timestamp()), 
                    "value": price,
                    "vol": vol, "amt": amt if amt else None, "chg": chg, "pct": pct
                })
            
                
        elif timeframe == '5日':
            # 新浪 5分钟级别 API (240根 = 5天)
            url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=5&ma=no&datalen=240"
            res = requests.get(url, timeout=5).json()
            
            prev_close = None
            for row in res:
                dt_str = f"{row['day']}+0800"
                dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S%z")
                
                op = float(row['open'])
                cl = float(row['close'])
                hi = float(row['high'])
                lo = float(row['low'])
                vol = float(row['volume']) / 1000000  # 股 → 万手
                
                if prev_close is None:
                    prev_close = op
                
                chg = cl - prev_close
                pct = (chg / prev_close) * 100 if prev_close else 0
                amp = ((hi - lo) / prev_close) * 100 if prev_close else 0

                results.append({
                    "time": int(dt.timestamp()),
                    "open": op, "high": hi, "low": lo, "close": cl,
                    "value": cl, # 为 AreaSeries 提供一类值
                    "vol": vol, "chg": chg, "pct": pct, "amp": amp,
                    "amt": (cl * vol / 100) # 粗略估算：成交量(万手) * 均价 / 100 = 亿 (成交额)
                })
                prev_close = cl
                
        else:
            # 日/周/月/年 → 优先东方财富(含真实成交额), 失败时回退腾讯
            em_prefix = '1' if symbol.startswith('sh') else '0'
            em_code = symbol[2:]
            klt_map = {'日K': 101, '周K': 102, '月K': 103, '年K': 106}
            klt = klt_map.get(timeframe, 101)
            em_url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
                      f"?secid={em_prefix}.{em_code}"
                      f"&fields1=f1,f2,f3,f4,f5,f6"
                      f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
                      f"&klt={klt}&fqt=0&beg=0&end=20500101&lmt=300")
            em_headers = {'Referer': 'https://quote.eastmoney.com/'}

            em_ok = False
            try:
                em_res = requests.get(em_url, headers=em_headers, timeout=5).json()
                klines = em_res.get('data', {}).get('klines') or []
                if klines:
                    em_ok = True
                    prev_close = None
                    for line in klines:
                        # "2026-04-17,open,close,high,low,vol(股),amt(元),amp%"
                        parts = line.split(',')
                        op  = float(parts[1])
                        cl  = float(parts[2])
                        hi  = float(parts[3])
                        lo  = float(parts[4])
                        vol = float(parts[5]) / 10000    # 手 → 万手
                        amt = float(parts[6]) / 100000000  # 元 → 亿
                        if prev_close is None:
                            prev_close = op
                        chg = cl - prev_close
                        pct = (chg / prev_close) * 100 if prev_close else 0
                        amp = float(parts[7]) if parts[7] not in ('-', '') else 0.0
                        results.append({
                            "time": parts[0],
                            "open": op, "close": cl, "high": hi, "low": lo,
                            "vol": vol, "amt": amt, "chg": chg, "pct": pct, "amp": amp
                        })
                        prev_close = cl
            except Exception:
                pass

            if not em_ok:
                # 回退：腾讯 newfqkline（含成交额，field[8]=万元）
                tf_map = {'日K': 'day', '周K': 'week', '月K': 'month', '年K': 'year'}
                tf_key = tf_map.get(timeframe, 'day')
                tc_url = f"http://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={symbol},{tf_key},,,300,qfq"
                tc_res = requests.get(tc_url, timeout=5).json()
                data_arr = tc_res.get('data', {}).get(symbol, {}).get(tf_key, [])
                prev_close = None
                for row in data_arr:
                    op = float(row[1]); cl = float(row[2])
                    hi = float(row[3]); lo = float(row[4])
                    vol = float(row[5]) / 10000 if len(row) > 5 else 0   # 手 → 万手
                    # field[8] = 成交额（万元），转成亿
                    amt = float(row[8]) / 10000 if len(row) > 8 and row[8] not in ('', '0.00', '0') else None
                    # field[7] = 振幅%
                    amp_raw = row[7] if len(row) > 7 else None
                    if prev_close is None:
                        prev_close = op
                    chg = cl - prev_close
                    pct = (chg / prev_close) * 100 if prev_close else 0
                    amp = float(amp_raw) if amp_raw and amp_raw not in ('', '0.00') else ((hi - lo) / prev_close * 100 if prev_close else 0)
                    results.append({
                        "time": row[0],
                        "open": op, "close": cl, "high": hi, "low": lo,
                        "vol": vol, "amt": amt, "chg": chg, "pct": pct, "amp": amp
                    })
                    prev_close = cl

    except Exception as e:
        print(f"Error fetching kline for {index_name} {timeframe}: {e}")
        
    return results

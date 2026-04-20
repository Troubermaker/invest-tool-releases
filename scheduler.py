import schedule
import time
import threading
import fetcher
from datetime import datetime

def job_fetch_all_data():
    print(f"[{datetime.now()}] [Scheduler] Running daily automated market data fetch...")
    try:
        fetcher.get_market_indices()
        fetcher.get_hot_sectors()
        print(f"[{datetime.now()}] [Scheduler] Done fetching.")
    except Exception as e:
        print(f"[{datetime.now()}] [Scheduler] Error: {e}")

def run_scheduler():
    # 每天 15:30 收盘后自动拉取一遍当天最新数据并缓存入库
    schedule.every().day.at("15:30").do(job_fetch_all_data)
    
    print("[Scheduler] Started background daemon. Waiting for tasks...")
    
    while True:
        schedule.run_pending()
        time.sleep(60) # check every minute

def start_background_daemon():
    """Start the scheduler in a background daemon thread so it doesn't block pywebview"""
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()

if __name__ == "__main__":
    job_fetch_all_data() # run once immediately if executed raw

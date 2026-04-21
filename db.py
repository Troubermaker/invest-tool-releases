import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'invest_data.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    # 市场行情缓存表
    c.execute('''
        CREATE TABLE IF NOT EXISTS market_cache (
            key TEXT PRIMARY KEY,
            data TEXT,
            updated_at TIMESTAMP
        )
    ''')
    # 自选股表
    c.execute('''
        CREATE TABLE IF NOT EXISTS watchlist (
            code TEXT PRIMARY KEY,
            name TEXT,
            added_at TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def set_cache(key, data):
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO market_cache (key, data, updated_at) 
        VALUES (?, ?, ?)
    ''', (key, json.dumps(data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_cache(key):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT data, updated_at FROM market_cache WHERE key = ?', (key,))
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row['data']), row['updated_at']
    return None, None

init_db()

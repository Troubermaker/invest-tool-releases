import time
import fetcher
import db
import ai_service
import json

class Api:
    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    def get_system_status(self):
        return {
            "status": "online",
            "message": "Desktop Backend (Python) connected!"
        }
        
    def get_market_data(self):
        """Fetch all necessary market overview data directly to frontend"""
        indices = fetcher.get_market_indices()
        sectors = fetcher.get_hot_sectors()
        
        return {
            "indices": indices,
            "hotSectors": sectors
        }
        
    def get_kline(self, name, timeframe):
        """Fetch historical multi-timeframe JSON for frontend Lightweight Charts"""
        return fetcher.get_kline_data(name, timeframe)
        
    def analyze_market_query(self, query):
        """Called by RightDrawer.vue to get AI analysis"""
        indices = fetcher.get_market_indices()
        sectors = fetcher.get_hot_sectors()
        
        context = json.dumps({
            "indices": indices,
            "top_hot_sectors": sectors[:3]  # Only pass top 3 to save tokens
        }, ensure_ascii=False)
        
        return ai_service.analyze_query(query, context=context)
        
    def refresh_cache(self):
        """Force manual refresh clearing the db cache"""
        conn = db.get_db()
        conn.execute("DELETE FROM market_cache")
        conn.commit()
        conn.close()
        return self.get_market_data()

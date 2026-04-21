import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from api import Api
import json

def verify_api():
    api = Api()
    print("Fetching market data through API...")
    data = api.get_market_data()
    
    print("API Response Keys:", list(data.keys()))
    
    # Validation checks
    if "indices" in data and isinstance(data["indices"], list):
        print("SUCCESS: 'indices' is a list.")
    else:
        print("ERROR: 'indices' is missing or not a list.")
        
    if "total_turnover" in data:
        print("SUCCESS: 'total_turnover' found: " + str(data['total_turnover']))
    else:
        print("ERROR: 'total_turnover' is missing.")
        
    if "hotSectors" in data:
        print("SUCCESS: 'hotSectors' found.")

if __name__ == "__main__":
    verify_api()

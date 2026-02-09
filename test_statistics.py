#!/home/charoyan/projects/tuya/venv/bin/python3
import json
from pathlib import Path
from datetime import datetime, timedelta
import tinytuya

CONFIG_FILE = Path(__file__).parent / "config.json"

def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

config = load_config()

cloud = tinytuya.Cloud(
    apiRegion=config["region"],
    apiKey=config["client_id"],
    apiSecret=config["client_secret"],
    apiDeviceID=config["device_id"]
)

socket_id = config.get("socket")
if not socket_id:
    print("No socket configured")
    exit(1)

# Test dates: last 7 days
end_date = datetime.now()
start_date = end_date - timedelta(days=7)

start_day = start_date.strftime("%Y%m%d")
end_day = end_date.strftime("%Y%m%d")

print(f"Requesting statistics from {start_day} to {end_day}")
print(f"Device ID: {socket_id}\n")

# Try the statistics endpoint
try:
    url = f'/v1.0/devices/{socket_id}/statistics/days'
    query = {
        'code': 'add_ele',
        'start_day': start_day,
        'end_day': end_day
    }
    print(f"URL: {url}")
    print(f"Query: {query}\n")
    response = cloud.cloudrequest(url, query=query)
    print("Response:")
    print(json.dumps(response, indent=2))
except Exception as e:
    print(f"Error: {e}")

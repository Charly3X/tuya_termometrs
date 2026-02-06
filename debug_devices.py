#!/home/charoyan/projects/tuya/venv/bin/python3
import json
from pathlib import Path
import tinytuya
import traceback

CONFIG_FILE = Path(__file__).parent / "config.json"

config = json.load(open(CONFIG_FILE))

cloud = tinytuya.Cloud(
    apiRegion=config["region"],
    apiKey=config["client_id"],
    apiSecret=config["client_secret"],
    apiDeviceID=config["device_id"]
)

for device_id in config["devices"]:
    print(f"\n=== Device: {device_id} ===")
    try:
        status = cloud.getstatus(device_id)
        print(f"Status: {json.dumps(status, indent=2)}")
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()

#!/home/charoyan/projects/tuya/venv/bin/python3
import json
import sys
from pathlib import Path
from datetime import datetime
import tinytuya

CONFIG_FILE = Path(__file__).parent / "config.json"
OUTPUT_FILE = Path(__file__).parent / "data.json"

def load_config():
    if not CONFIG_FILE.exists():
        return None
    with open(CONFIG_FILE) as f:
        return json.load(f)

def get_temperatures():
    config = load_config()
    if not config:
        return {"temperatures": ["-", "-", "-"], "humidity": ["-", "-", "-"], "names": ["No config", "", ""], "batteries": [0, 0, 0], "socket": {}}
    
    cloud = tinytuya.Cloud(
        apiRegion=config["region"],
        apiKey=config["client_id"],
        apiSecret=config["client_secret"],
        apiDeviceID=config["device_id"]
    )
    
    # Get all devices once to get names
    all_devices = cloud.getdevices()
    device_map = {d["id"]: d["name"] for d in all_devices}
    
    devices = config["devices"]
    temps = []
    humids = []
    names = []
    batteries = []
    
    for device_id in devices:
        try:
            device_name = device_map.get(device_id, device_id[:8])
            
            # Get status
            status = cloud.getstatus(device_id)
            temp = None
            humid = None
            battery = None
            
            if status.get("result"):
                for item in status.get("result", []):
                    if item["code"] in ["va_temperature", "temp_current", "temperature"]:
                        temp = item["value"] / 10 if item["value"] > 100 else item["value"]
                    elif item["code"] in ["va_humidity", "humidity_value", "humidity"]:
                        humid = item["value"]
                    elif item["code"] in ["battery_state", "battery_percentage"]:
                        battery = item["value"]
            
            # If no data, try shadow properties (for battery devices)
            if temp is None or battery is None:
                try:
                    shadow = cloud.cloudrequest(
                        f'/v2.0/cloud/thing/{device_id}/shadow/properties',
                        action='GET'
                    )
                    if shadow.get("success") and shadow.get("result", {}).get("properties"):
                        for prop in shadow["result"]["properties"]:
                            if prop["code"] in ["temp_current", "temperature"] and temp is None:
                                temp = prop["value"] / 10 if prop["value"] > 100 else prop["value"]
                            elif prop["code"] in ["humidity_value", "humidity"] and humid is None:
                                humid = prop["value"]
                            elif prop["code"] in ["battery_state", "battery_percentage"] and battery is None:
                                battery = prop["value"]
                except:
                    pass
            
            temps.append(f"{temp:.1f}" if temp is not None else "--")
            humids.append(f"{humid}" if humid is not None else "--")
            names.append(device_name)
            
            # Convert battery state to percentage
            if isinstance(battery, str):
                battery_map = {"high": 80, "middle": 40, "low": 10}
                batteries.append(battery_map.get(battery, 50))
            elif isinstance(battery, (int, float)):
                batteries.append(int(battery))
            else:
                batteries.append(100)  # No battery info = assume powered
                
        except Exception as e:
            temps.append("ERR")
            humids.append("ERR")
            names.append("Error")
            batteries.append(0)
    
    # Get socket data
    socket_data = {"name": "", "power": "--", "voltage": "--", "energy": "--"}
    if "socket" in config:
        try:
            socket_id = config["socket"]
            socket_data["name"] = device_map.get(socket_id, "Socket")
            
            status = cloud.getstatus(socket_id)
            if status.get("result"):
                for item in status.get("result", []):
                    if item["code"] == "cur_power":
                        socket_data["power"] = f"{item['value'] / 10:.1f}"
                    elif item["code"] == "cur_voltage":
                        socket_data["voltage"] = f"{item['value'] / 10:.0f}"
                    elif item["code"] == "add_ele":
                        socket_data["energy"] = f"{item['value'] / 1000:.2f}"
        except Exception as e:
            pass
    
    return {"temperatures": temps, "humidity": humids, "names": names, "batteries": batteries, "socket": socket_data, "last_update": datetime.now().strftime("%H:%M:%S")}

if __name__ == "__main__":
    result = get_temperatures()
    
    # Write to file for widget to read
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(result, f)
    
    # Also print for direct use
    print(json.dumps(result))

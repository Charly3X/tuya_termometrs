#!/home/charoyan/projects/tuya/venv/bin/python3
import json
import sys
from pathlib import Path
from datetime import datetime
import tinytuya

CONFIG_FILE = Path(__file__).parent / "config.json"
OUTPUT_FILE = Path(__file__).parent / "data.json"
CACHE_FILE = Path(__file__).parent / "device_names_cache.json"
LOG_FILE = Path(__file__).parent / "api_calls.log"

def log_api_call(message):
    with open(LOG_FILE, 'a') as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")

def load_config():
    if not CONFIG_FILE.exists():
        return None
    with open(CONFIG_FILE) as f:
        return json.load(f)

def load_device_names_cache():
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
            # Check if cache is older than 24 hours
            cache_time = datetime.fromisoformat(cache.get("timestamp", "2000-01-01T00:00:00"))
            if (datetime.now() - cache_time).total_seconds() > 86400:
                return None
            return cache.get("names", {})
    except:
        return None

def save_device_names_cache(names):
    with open(CACHE_FILE, 'w') as f:
        json.dump({"timestamp": datetime.now().isoformat(), "names": names}, f)

def get_temperatures():
    log_api_call("=== Widget update started ===")
    config = load_config()
    if not config:
        return {"temperatures": ["-", "-", "-"], "humidity": ["-", "-", "-"], "names": ["No config", "", ""], "batteries": [0, 0, 0], "socket": {}}
    
    cloud = tinytuya.Cloud(
        apiRegion=config["region"],
        apiKey=config["client_id"],
        apiSecret=config["client_secret"],
        apiDeviceID=config["device_id"]
    )
    
    # Load or fetch device names
    device_map = load_device_names_cache()
    if device_map is None:
        log_api_call("API CALL: cloud.getdevices() - fetching device names")
        all_devices = cloud.getdevices()
        device_map = {d["id"]: d["name"] for d in all_devices}
        save_device_names_cache(device_map)
    else:
        log_api_call("Using cached device names")
    
    devices = config["devices"]
    temps = []
    humids = []
    names = []
    batteries = []
    
    # Get status for all devices in one batch request
    all_device_ids = devices.copy()
    if "socket" in config:
        all_device_ids.append(config["socket"])
    
    device_ids_str = ",".join(all_device_ids)
    log_api_call(f"API CALL: batch status request for {len(all_device_ids)} devices")
    batch_response = cloud.cloudrequest(
        f'/v1.0/iot-03/devices/status?device_ids={device_ids_str}',
        action='GET'
    )
    
    # Create a map of device_id -> status for quick lookup
    status_map = {}
    if batch_response.get("success") and batch_response.get("result"):
        for device_data in batch_response["result"]:
            status_map[device_data["id"]] = device_data.get("status", [])
    
    # Process thermometers
    for device_id in devices:
        try:
            device_name = device_map.get(device_id, device_id[:8])
            temp = None
            humid = None
            battery = None
            
            # Try to get from batch response first
            status_list = status_map.get(device_id, [])
            for item in status_list:
                if item["code"] in ["temp_current", "temperature", "va_temperature"]:
                    temp = item["value"] / 10 if item["value"] > 100 else item["value"]
                elif item["code"] in ["humidity_value", "humidity", "va_humidity"]:
                    humid = item["value"]
                elif item["code"] in ["battery_state", "battery_percentage"]:
                    battery = item["value"]
            
            # If no data from batch, try shadow API as fallback
            if temp is None:
                log_api_call(f"API CALL: shadow properties for device {device_id[:8]} (fallback)")
                shadow = cloud.cloudrequest(
                    f'/v2.0/cloud/thing/{device_id}/shadow/properties',
                    action='GET'
                )
                if shadow.get("success") and shadow.get("result", {}).get("properties"):
                    for prop in shadow["result"]["properties"]:
                        if prop["code"] in ["temp_current", "temperature"]:
                            temp = prop["value"] / 10 if prop["value"] > 100 else prop["value"]
                        elif prop["code"] in ["humidity_value", "humidity"]:
                            humid = prop["value"]
                        elif prop["code"] in ["battery_state", "battery_percentage"]:
                            battery = prop["value"]
            
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
                batteries.append(100)
                
        except Exception as e:
            temps.append("ERR")
            humids.append("ERR")
            names.append("Error")
            batteries.append(0)
    
    # Get socket data from batch response
    socket_data = {"name": "", "power": "--", "voltage": "--", "energy": "--"}
    if "socket" in config:
        try:
            socket_id = config["socket"]
            socket_data["name"] = device_map.get(socket_id, "Socket")
            
            # Use data from batch response
            status_list = status_map.get(socket_id, [])
            for item in status_list:
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
    log_api_call(f"=== Widget update finished ===\n")
    
    # Write to file for widget to read
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(result, f)
    
    # Also print for direct use
    print(json.dumps(result))

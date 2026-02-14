#!/home/charoyan/projects/tuya/venv/bin/python3
import json
import sys
from pathlib import Path
from datetime import datetime
import tinytuya

CONFIG_FILE = Path(__file__).parent / "config.json"
OUTPUT_FILE = Path(__file__).parent / "data.json"
CACHE_FILE = Path(__file__).parent / "device_names_cache.json"
TOKEN_CACHE_FILE = Path(__file__).parent / "token_cache.json"
LOG_FILE = Path(__file__).parent / "api_calls.log"

def log_api_call(message):
    with open(LOG_FILE, 'a') as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")

def trim_log_file():
    if not LOG_FILE.exists():
        return
    
    with open(LOG_FILE, 'r') as f:
        lines = f.readlines()
    
    # Find last 3 "Widget update started" occurrences
    starts = [i for i, line in enumerate(lines) if "Widget update started" in line]
    
    if len(starts) > 3:
        # Keep only last 3 cycles
        keep_from = starts[-3]
        with open(LOG_FILE, 'w') as f:
            f.writelines(lines[keep_from:])

def load_config():
    if not CONFIG_FILE.exists():
        return None
    with open(CONFIG_FILE) as f:
        return json.load(f)

def load_token_cache():
    if not TOKEN_CACHE_FILE.exists():
        return None
    try:
        with open(TOKEN_CACHE_FILE) as f:
            cache = json.load(f)
            expire_time = datetime.fromisoformat(cache.get("expire_time", "2000-01-01T00:00:00"))
            if datetime.now() < expire_time:
                return cache.get("token")
    except:
        pass
    return None

def save_token_cache(token):
    expire_time = datetime.now().timestamp() + 3600  # 1 hour
    with open(TOKEN_CACHE_FILE, 'w') as f:
        json.dump({
            "token": token,
            "expire_time": datetime.fromtimestamp(expire_time).isoformat()
        }, f)

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

def get_cloud_and_device_map(config):
    cached_token = load_token_cache()
    if cached_token:
        log_api_call("Using cached token")
    else:
        log_api_call("Getting new token")
    
    cloud = tinytuya.Cloud(
        apiRegion=config["region"],
        apiKey=config["client_id"],
        apiSecret=config["client_secret"],
        apiDeviceID=config["device_id"],
        initial_token=cached_token
    )
    
    if not cached_token:
        save_token_cache(cloud.token)
    
    device_map = load_device_names_cache()
    if device_map is None:
        log_api_call("API CALL: cloud.getdevices() - fetching device names")
        all_devices = cloud.getdevices()
        device_map = {d["id"]: d["name"] for d in all_devices}
        save_device_names_cache(device_map)
    else:
        log_api_call("Using cached device names")
    
    return cloud, device_map

def get_temperatures():
    config = load_config()
    if not config:
        return {"temperatures": ["-", "-", "-"], "humidity": ["-", "-", "-"], "names": ["No config", "", ""], "batteries": [0, 0, 0]}
    
    cloud, device_map = get_cloud_and_device_map(config)
    devices = config["devices"]
    
    log_api_call(f"API CALL: batch status request for {len(devices)} thermometers")
    device_ids_str = ",".join(devices)
    batch_response = cloud.cloudrequest(
        f'/v1.0/iot-03/devices/status?device_ids={device_ids_str}',
        action='GET'
    )
    
    status_map = {}
    if batch_response.get("success") and batch_response.get("result"):
        for device_data in batch_response["result"]:
            status_map[device_data["id"]] = device_data.get("status", [])
    
    temps = []
    humids = []
    names = []
    batteries = []
    
    for device_id in devices:
        try:
            device_name = device_map.get(device_id, device_id[:8])
            temp = None
            humid = None
            battery = None
            
            status_list = status_map.get(device_id, [])
            for item in status_list:
                if item["code"] in ["temp_current", "temperature", "va_temperature"]:
                    temp = item["value"] / 10 if item["value"] > 100 else item["value"]
                elif item["code"] in ["humidity_value", "humidity", "va_humidity"]:
                    humid = item["value"]
                elif item["code"] in ["battery_state", "battery_percentage"]:
                    battery = item["value"]
            
            if temp is None:
                log_api_call(f"API CALL: shadow properties for device {device_id[:8]} (fallback)")
                shadow = cloud.cloudrequest(
                    f'/v2.0/cloud/thing/{device_id}/shadow/properties',
                    action='GET'
                )
                if shadow.get("success") and shadow.get("result", {}).get("properties"):
                    for prop in shadow["result"]["properties"]:
                        if prop["code"] in ["temp_current", "temperature", "va_temperature"]:
                            temp = prop["value"] / 10 if prop["value"] > 100 else prop["value"]
                        elif prop["code"] in ["humidity_value", "humidity", "va_humidity"]:
                            humid = prop["value"]
                        elif prop["code"] in ["battery_state", "battery_percentage"]:
                            battery = prop["value"]
            
            temps.append(f"{temp:.1f}" if temp is not None else "--")
            humids.append(f"{humid}" if humid is not None else "--")
            names.append(device_name)
            
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
    
    return {"temperatures": temps, "humidity": humids, "names": names, "batteries": batteries}

def get_socket_data():
    config = load_config()
    if not config or "socket" not in config:
        return {"socket": {"name": "", "power": "--", "voltage": "--", "energy": "--"}}
    
    cloud, device_map = get_cloud_and_device_map(config)
    socket_id = config["socket"]
    
    log_api_call(f"API CALL: status request for socket {socket_id[:8]}")
    batch_response = cloud.cloudrequest(
        f'/v1.0/iot-03/devices/status?device_ids={socket_id}',
        action='GET'
    )
    log_api_call(f"RESPONSE: {json.dumps(batch_response)}")
    
    socket_data = {"name": device_map.get(socket_id, "Socket"), "power": "--", "voltage": "--", "energy": "--"}
    
    if batch_response.get("success") and batch_response.get("result"):
        status_list = batch_response["result"][0].get("status", [])
        for item in status_list:
            if item["code"] == "cur_power":
                socket_data["power"] = f"{item['value'] / 10:.1f}"
            elif item["code"] == "cur_voltage":
                socket_data["voltage"] = f"{item['value'] / 10:.0f}"
            elif item["code"] == "add_ele":
                socket_data["energy"] = f"{item['value'] / 1000:.2f}"
    
    return {"socket": socket_data}

def get_all_data():
    config = load_config()
    if not config:
        return {"temperatures": ["-", "-", "-"], "humidity": ["-", "-", "-"], "names": ["No config", "", ""], "batteries": [0, 0, 0], "socket": {}}
    
    cloud, device_map = get_cloud_and_device_map(config)
    
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
    log_api_call(f"RESPONSE: {json.dumps(batch_response)}")
    
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
                log_api_call(f"RESPONSE: {json.dumps(shadow)}")
                if shadow.get("success") and shadow.get("result", {}).get("properties"):
                    for prop in shadow["result"]["properties"]:
                        if prop["code"] in ["temp_current", "temperature", "va_temperature"]:
                            temp = prop["value"] / 10 if prop["value"] > 100 else prop["value"]
                        elif prop["code"] in ["humidity_value", "humidity", "va_humidity"]:
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
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    if mode == "thermometers":
        log_api_call("=== Thermometer update started ===")
        result = get_temperatures()
        log_api_call("=== Thermometer update finished ===\n")
    elif mode == "socket":
        log_api_call("=== Socket update started ===")
        result = get_socket_data()
        log_api_call("=== Socket update finished ===\n")
    else:
        log_api_call("=== Full update started ===")
        result = get_all_data()
        log_api_call("=== Full update finished ===\n")
    
    trim_log_file()
    
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(result, f)
    
    print(json.dumps(result))

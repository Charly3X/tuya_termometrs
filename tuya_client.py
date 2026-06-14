#!/home/charoyan/projects/tuya/venv/bin/python3
import json
import sys
from pathlib import Path
from datetime import datetime
import tinytuya
from tuya_local import (
    get_local_temperatures,
    get_local_socket_data,
    get_local_all_data,
    log_local_call
)
from tuya_history import add_readings, get_device_history

CONFIG_FILE = Path(__file__).parent / "config.json"
OUTPUT_FILE = Path(__file__).parent / "data.json"
CACHE_FILE = Path(__file__).parent / "device_names_cache.json"
TOKEN_CACHE_FILE = Path(__file__).parent / "token_cache.json"
LOG_FILE = Path(__file__).parent / "api_calls.log"
LOGGING_ENABLED = False

def log_api_call(message):
    if not LOGGING_ENABLED:
        return
    with open(LOG_FILE, 'a') as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")

def trim_log_file():
    if not LOGGING_ENABLED or not LOG_FILE.exists():
        return
    
    with open(LOG_FILE, 'r') as f:
        lines = f.readlines()
    
    # Find last 3 update cycles (any type)
    starts = [i for i, line in enumerate(lines) if "update started" in line]
    
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

def load_device_names_cache(ignore_expiry=False):
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
            if not ignore_expiry:
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
        log_api_call("API CALL: fetching device names")
        device_map = {}
        old_cache = load_device_names_cache(ignore_expiry=True) or {}
        has_errors = False
        
        # Build list of all configured devices
        all_devices = list(config.get("devices", []))
        if "socket" in config:
            all_devices.append(config["socket"])
            
        for device_id in all_devices:
            # Check local config first just in case
            local_name = None
            for device_conf in config.get("local_devices", []):
                if device_conf.get("id") == device_id:
                    local_name = device_conf.get("name")
            if "local_socket" in config and config["local_socket"].get("id") == device_id:
                local_name = config["local_socket"].get("name")
            
            if local_name:
                device_map[device_id] = local_name
                continue
                
            # Fetch from Tuya Cloud API
            fallback_name = old_cache.get(device_id) or ("Socket" if device_id == config.get("socket") else device_id[:8])
            try:
                response = cloud.cloudrequest(f'/v1.0/devices/{device_id}', action='GET')
                if response and response.get('success'):
                    device_map[device_id] = response.get('result', {}).get('name', fallback_name)
                else:
                    device_map[device_id] = fallback_name
                    has_errors = True
                    log_api_call(f"Failed to fetch name for {device_id}, using fallback")
            except Exception as e:
                device_map[device_id] = fallback_name
                has_errors = True
                log_api_call(f"Error fetching name for {device_id}: {str(e)}")
                
        if not has_errors:
            save_device_names_cache(device_map)
        else:
            log_api_call("API returned errors, skipped saving cache to retry next time")
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
    LOGGING_ENABLED = "--log" in sys.argv
    
    # Parse arguments
    args = [arg for arg in sys.argv[1:] if arg != "--log"]
    mode = args[0] if len(args) > 0 else "all"
    connection_mode = args[1] if len(args) > 1 else "cloud"
    
    # History mode: output chart data and exit
    if mode == "history":
        device_id = args[1] if len(args) > 1 else ""
        hours = int(args[2]) if len(args) > 2 else 1
        history = get_device_history(device_id, hours)
        print(json.dumps({"history": history, "history_device": device_id}))
        sys.exit(0)
    
    # Load config
    config = load_config()
    
    # Smart mode: try local first, fallback to cloud
    if connection_mode == "smart":
        log_api_call("=== SMART MODE: trying local first ===")
        
        # Check if local config exists
        has_local_config = config and (
            config.get('local_devices') or config.get('local_socket')
        )
        
        if has_local_config:
            log_api_call("Local config found, attempting local connection...")
            try:
                if mode == "thermometers":
                    result = get_local_temperatures(config, log_api_call if LOGGING_ENABLED else None)
                elif mode == "socket":
                    result = get_local_socket_data(config, log_api_call if LOGGING_ENABLED else None)
                else:
                    result = get_local_all_data(config, log_api_call if LOGGING_ENABLED else None)
                
                # Check if we got valid data
                has_thermo_data = False
                has_socket_data = False
                
                if mode == "thermometers" or mode == "all":
                    has_thermo_data = any(t != "-" and t != "--" for t in result.get("temperatures", []))
                
                if mode == "socket" or mode == "all":
                    socket_data = result.get("socket", {})
                    has_socket_data = socket_data.get("power") not in ["--", "-", ""]
                
                # If we got some data locally, check what's missing
                if mode == "all":
                    # For "all" mode, we need both thermometers and socket
                    # If thermometers failed but socket worked, get thermometers from cloud
                    if has_socket_data and not has_thermo_data:
                        log_api_call("Socket data OK locally, but thermometers failed. Getting thermometers from cloud...")
                        cloud_result = get_temperatures()
                        result["temperatures"] = cloud_result["temperatures"]
                        result["humidity"] = cloud_result["humidity"]
                        result["names"] = cloud_result["names"]
                        result["batteries"] = cloud_result["batteries"]
                        log_api_call("Smart mode: mixed (socket local, thermometers cloud)")
                    elif has_thermo_data or has_socket_data:
                        log_api_call("Local connection successful!")
                    else:
                        log_api_call("Local connection returned no data, falling back to cloud...")
                        raise Exception("No local data")
                elif mode == "thermometers":
                    if not has_thermo_data:
                        log_api_call("No thermometer data locally, falling back to cloud...")
                        raise Exception("No thermometer data")
                    else:
                        log_api_call("Local connection successful!")
                elif mode == "socket":
                    if not has_socket_data:
                        log_api_call("No socket data locally, falling back to cloud...")
                        raise Exception("No socket data")
                    else:
                        log_api_call("Local connection successful!")
                    
            except Exception as e:
                log_api_call(f"Local connection failed: {str(e)}, using cloud...")
                # Fallback to cloud
                if mode == "thermometers":
                    result = get_temperatures()
                elif mode == "socket":
                    result = get_socket_data()
                else:
                    result = get_all_data()
        else:
            log_api_call("No local config, using cloud...")
            # No local config, use cloud
            if mode == "thermometers":
                result = get_temperatures()
            elif mode == "socket":
                result = get_socket_data()
            else:
                result = get_all_data()
        
        log_api_call("=== SMART MODE: finished ===\n")
    
    # Local mode (hybrid: sockets local, thermometers cloud fallback)
    elif connection_mode == "local":
        log_api_call(f"=== LOCAL MODE: {mode} update started ===")
        
        try:
            if mode == "socket" or mode == "all":
                if mode == "socket":
                    result = get_local_socket_data(config, log_api_call if LOGGING_ENABLED else None)
                else: # mode == "all"
                    result = get_local_all_data(config, log_api_call if LOGGING_ENABLED else None)
                    # For thermostats, cloud fallback is often necessary (battery sleep)
                    if not result or not any(t != "-" and t != "--" for t in result.get("temperatures", [])):
                        log_api_call("LOCAL: Thermometers unreachable locally, using cloud...")
                        cloud_res = get_temperatures()
                        if result is None: result = {}
                        result.update({k: cloud_res[k] for k in ["temperatures", "humidity", "names", "batteries"] if k in cloud_res})
            
            elif mode == "thermometers":
                result = get_local_temperatures(config, log_api_call if LOGGING_ENABLED else None)
                if not any(t != "-" and t != "--" for t in result.get("temperatures", [])):
                    log_api_call("LOCAL: Thermometers unreachable locally, using cloud...")
                    result = get_temperatures()
        except Exception as e:
            log_api_call(f"LOCAL MODE CRITICAL ERROR: {str(e)}. Falling back to cloud only for thermometers if relevant.")
            if mode == "thermometers": result = get_temperatures()
            else: result = get_all_data() # Full fallback as last resort
        
        log_api_call(f"=== LOCAL MODE: {mode} update finished ===\n")
    
    # Cloud mode (default)
    else:
        log_api_call(f"=== CLOUD MODE: {mode} update started ===")
        
        if mode == "thermometers":
            result = get_temperatures()
        elif mode == "socket":
            result = get_socket_data()
        else:
            result = get_all_data()
        
        log_api_call(f"=== CLOUD MODE: {mode} update finished ===\n")
    
    trim_log_file()
    
    # Save socket readings to history
    sockets_list = result.get("sockets", [])
    if not sockets_list and "socket" in result:
        sockets_list = [result["socket"]]
    if sockets_list:
        try:
            add_readings(sockets_list)
        except Exception:
            pass
    
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(result, f)
    
    print(json.dumps(result))

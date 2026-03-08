#!/usr/bin/env python3
"""
Local network communication module for Tuya devices.
Uses tinytuya to communicate directly with devices on the local network.
"""
import tinytuya
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

def log_local_call(message, log_func):
    """Log local network calls if logging is enabled."""
    if log_func:
        log_func(message)

def get_local_device_status(device_config, log_func=None, refresh_dps=None):
    """
    Get status from a local Tuya device.
    
    Args:
        device_config: dict with keys: id, ip, local_key, version
        log_func: optional logging function
        refresh_dps: optional list of DPS indices to force refresh before reading
    
    Returns:
        dict with device status or None on error
    """
    try:
        log_local_call(f"LOCAL: Connecting to device {device_config['id'][:8]} at {device_config['ip']}", log_func)
        
        device = tinytuya.Device(
            dev_id=device_config['id'],
            address=device_config['ip'],
            local_key=device_config['local_key'],
            version=float(device_config.get('version', '3.3'))
        )
        
        # Set connection timeout
        device.set_socketTimeout(2)
        
        # Force refresh specific DPS before reading (e.g. power monitoring)
        if refresh_dps:
            device.updatedps(refresh_dps)
        
        # Get device status
        status = device.status()
        log_local_call(f"LOCAL: Response from {device_config['id'][:8]}: {status}", log_func)
        
        return status
    except Exception as e:
        log_local_call(f"LOCAL ERROR: Failed to get status from {device_config['id'][:8]}: {str(e)}", log_func)
        return None

def parse_thermometer_status(status, device_name):
    """
    Parse thermometer/hygrometer status from local device response.
    
    Args:
        status: device status dict from tinytuya
        device_name: name of the device
    
    Returns:
        tuple: (temperature, humidity, battery)
    """
    if not status or 'dps' not in status:
        return None, None, None
    
    dps = status['dps']
    temp = None
    humid = None
    battery = None
    
    # Common DPS codes for temperature/humidity sensors
    # DPS 1: temperature (in 0.1°C or °C)
    # DPS 2: humidity (in %)
    # DPS 4: battery state or percentage
    
    for key, value in dps.items():
        key_str = str(key)
        
        # Temperature
        if key_str in ['1', '101', '102']:
            if isinstance(value, (int, float)):
                temp = value / 10 if value > 100 else value
        
        # Humidity
        elif key_str in ['2', '103', '104']:
            if isinstance(value, (int, float)):
                humid = value
        
        # Battery
        elif key_str in ['4', '14', '15']:
            if isinstance(value, str):
                battery_map = {"high": 80, "middle": 40, "low": 10}
                battery = battery_map.get(value.lower(), 50)
            elif isinstance(value, (int, float)):
                battery = int(value)
    
    return temp, humid, battery

def parse_socket_status(status):
    """
    Parse smart plug status from local device response.
    
    Args:
        status: device status dict from tinytuya
    
    Returns:
        dict with power, voltage, energy
    """
    result = {"power": "--", "voltage": "--", "energy": "--"}
    
    if not status or 'dps' not in status:
        return result
    
    dps = status['dps']
    
    # DPS code mapping (varies by device model):
    #
    # Standard mapping:
    # DPS 18: current (mA)
    # DPS 19: power (0.1W)
    # DPS 20: voltage (0.1V)
    # DPS 101: energy (0.001 kWh)
    #
    # T34-Smart Plug+ mapping:
    # DPS 20: energy add_ele (0.01 kWh)
    # DPS 21: current cur_current (mA)
    # DPS 22: power cur_power (0.1W)
    # DPS 23: voltage cur_voltage (0.1V)
    
    for key, value in dps.items():
        key_str = str(key)
        
        if key_str in ['19', '5', '22']:  # Power (0.1W)
            if isinstance(value, (int, float)):
                result["power"] = f"{value / 10:.1f}"
        
        elif key_str in ['20', '6']:  # Voltage (0.1V) OR Energy (0.01 kWh)
            if isinstance(value, (int, float)):
                # Check if this looks like voltage (>1000) or energy (<1000)
                if value > 1000:
                    result["voltage"] = f"{value / 10:.0f}"
                else:
                    result["energy"] = f"{value / 100:.2f}"
        
        elif key_str in ['23']:  # Voltage for T34-Smart Plug+ (0.1V)
            if isinstance(value, (int, float)):
                result["voltage"] = f"{value / 10:.0f}"
        
        elif key_str in ['101', '17']:  # Energy (0.001 kWh)
            if isinstance(value, (int, float)):
                result["energy"] = f"{value / 1000:.2f}"
    
    return result

def get_local_temperatures(config, log_func=None):
    """
    Get temperature/humidity data from local devices.
    
    Args:
        config: configuration dict with 'local_devices' list
        log_func: optional logging function
    
    Returns:
        dict with temperatures, humidity, names, batteries
    """
    local_devices = config.get('local_devices', [])
    
    temps = []
    humids = []
    names = []
    batteries = []
    
    for device_config in local_devices[:3]:  # Max 3 devices
        device_name = device_config.get('name', device_config['id'][:8])
        status = get_local_device_status(device_config, log_func)
        
        temp, humid, battery = parse_thermometer_status(status, device_name)
        
        temps.append(f"{temp:.1f}" if temp is not None else "--")
        humids.append(f"{humid}" if humid is not None else "--")
        names.append(device_name)
        batteries.append(battery if battery is not None else 100)
    
    # Pad to 3 devices if needed
    while len(temps) < 3:
        temps.append("-")
        humids.append("-")
        names.append("")
        batteries.append(0)
    
    return {
        "temperatures": temps,
        "humidity": humids,
        "names": names,
        "batteries": batteries
    }

def get_local_socket_data(config, log_func=None):
    """
    Get smart plug data from local devices.
    Supports both single socket (local_socket) and multiple sockets (local_sockets).
    
    Args:
        config: configuration dict with 'local_socket' or 'local_sockets'
        log_func: optional logging function
    
    Returns:
        dict with sockets data (array for multiple sockets)
    """
    # Support both old (single) and new (multiple) format
    sockets = []
    
    if 'local_sockets' in config:
        sockets = config['local_sockets']
    elif 'local_socket' in config:
        sockets = [config['local_socket']]
    
    if not sockets:
        return {"sockets": []}
    
    result = []
    
    for socket_config in sockets:
        socket_name = socket_config.get('name', 'Socket')
        # Force refresh power monitoring DPS for real-time readings
        status = get_local_device_status(socket_config, log_func, refresh_dps=[18, 19, 20, 21, 22, 23])
        socket_data = parse_socket_status(status)
        socket_data["name"] = socket_name
        socket_data["id"] = socket_config['id']
        result.append(socket_data)
    
    # For backward compatibility, also return single socket format
    if len(result) == 1:
        return {"socket": result[0], "sockets": result}
    
    return {"sockets": result}

def get_local_all_data(config, log_func=None):
    """
    Get all data from local devices (thermometers + sockets).
    
    Args:
        config: configuration dict
        log_func: optional logging function
    
    Returns:
        dict with all device data
    """
    result = get_local_temperatures(config, log_func)
    socket_data = get_local_socket_data(config, log_func)
    
    # Merge socket data
    if "socket" in socket_data:
        result["socket"] = socket_data["socket"]
    if "sockets" in socket_data:
        result["sockets"] = socket_data["sockets"]
    
    result["last_update"] = datetime.now().strftime("%H:%M:%S")
    
    return result

#!/usr/bin/env python3
"""
History storage for Tuya power monitoring data.
Stores readings in a compact JSON file.
"""
import json
from pathlib import Path
from datetime import datetime, timedelta

HISTORY_FILE = Path(__file__).parent / "power_history.json"
MAX_ENTRIES_PER_DEVICE = 8640  # 24h at 10-second intervals


def load_history():
    if not HISTORY_FILE.exists():
        return {}
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except Exception as e:
        # If the file is empty or corrupted, we will return {}
        # but let's at least not do it on a half-written file anymore.
        return {}


def save_history(history):
    temp_file = HISTORY_FILE.with_name(HISTORY_FILE.name + '.tmp')
    try:
        with open(temp_file, 'w') as f:
            json.dump(history, f)
        temp_file.rename(HISTORY_FILE)
    except Exception:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass


def add_readings(socket_results):
    """
    Save power readings for multiple sockets.
    
    Args:
        socket_results: list of dicts with 'id', 'power', 'voltage' keys
    """
    history = load_history()
    timestamp = int(datetime.now().timestamp())
    
    for sock in socket_results:
        device_id = sock.get("id", "")
        if not device_id:
            continue
        
        try:
            power = float(sock.get("power", 0))
        except (ValueError, TypeError):
            continue
        
        try:
            voltage = float(sock.get("voltage", 0))
        except (ValueError, TypeError):
            voltage = 0
        
        if device_id not in history:
            history[device_id] = []
        
        # [timestamp, power, voltage]
        history[device_id].append([timestamp, power, voltage])
        
        # Trim to max entries
        if len(history[device_id]) > MAX_ENTRIES_PER_DEVICE:
            history[device_id] = history[device_id][-MAX_ENTRIES_PER_DEVICE:]
    
    save_history(history)


def get_device_history(device_id, hours=24):
    """
    Get history for a device within the last N hours.
    
    Returns:
        list of [timestamp, power, voltage] entries
    """
    history = load_history()
    
    if device_id not in history:
        return []
    
    cutoff = int((datetime.now() - timedelta(hours=hours)).timestamp())
    
    return [entry for entry in history[device_id] if entry[0] >= cutoff]

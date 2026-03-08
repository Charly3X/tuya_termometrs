#!/usr/bin/env python3
"""
Helper script to get local keys for Tuya devices.
This script uses the cloud API to retrieve device information including local keys.
"""
import json
from pathlib import Path
import tinytuya

CONFIG_FILE = Path(__file__).parent / "config.json"

def get_device_local_keys():
    """Get local keys for all devices from cloud API."""
    if not CONFIG_FILE.exists():
        print("Error: config.json not found")
        return
    
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    
    print("Connecting to Tuya Cloud API...")
    cloud = tinytuya.Cloud(
        apiRegion=config["region"],
        apiKey=config["client_id"],
        apiSecret=config["client_secret"],
        apiDeviceID=config["device_id"]
    )
    
    print("\nFetching device list...")
    
    # Get list of device IDs
    device_ids = config.get("devices", [])
    if "socket" in config:
        device_ids.append(config["socket"])
    
    if not device_ids:
        print("No devices configured in config.json")
        return
    
    print(f"\nFound {len(device_ids)} devices in config:\n")
    print("=" * 80)
    
    # Get detailed info for each device using direct API call
    for device_id in device_ids:
        try:
            # Use direct API request to get device details
            response = cloud.cloudrequest(f'/v1.0/devices/{device_id}', action='GET')
            
            if response and response.get('success'):
                result = response.get('result', {})
                name = result.get('name', 'Unknown')
                local_key = result.get('local_key', 'N/A')
                ip = result.get('ip', 'Not available')
                product_name = result.get('product_name', 'Unknown')
                online = result.get('online', False)
                
                print(f"Device: {name}")
                print(f"  ID: {device_id}")
                print(f"  Local Key: {local_key}")
                print(f"  IP: {ip}")
                print(f"  Product: {product_name}")
                print(f"  Online: {'Yes' if online else 'No'}")
                print("-" * 80)
            else:
                print(f"Device ID: {device_id}")
                print(f"  Error: {response.get('msg', 'Could not fetch details')}")
                print("-" * 80)
        except Exception as e:
            print(f"Device ID: {device_id}")
            print(f"  Error: {str(e)}")
            print("-" * 80)
    
    print("\nTo use local network mode:")
    print("1. If Cloud API subscription expired:")
    print("   - Use: python3 -m tinytuya wizard")
    print("   - Or see MANUAL_LOCAL_SETUP.md for packet capture method")
    print("2. Find device IPs:")
    print("   - Run: ./venv/bin/python3 scan_local_devices.py")
    print("   - Or check your router's DHCP table")
    print("3. Add local_devices section to config.json with:")
    print("   - id: device ID from above")
    print("   - name: friendly name")
    print("   - ip: local IP address")
    print("   - local_key: from wizard or packet capture")
    print("   - version: from scan (usually 3.3, 3.4, or 3.5)")
    print("\nSee MANUAL_LOCAL_SETUP.md for detailed instructions.")

if __name__ == "__main__":
    get_device_local_keys()

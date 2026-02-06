#!/home/charoyan/projects/tuya/venv/bin/python3
import tinytuya

CLIENT_ID = "YOUR_CLIENT_ID"
CLIENT_SECRET = "YOUR_CLIENT_SECRET"

regions = ["eu", "us", "cn", "in", "ue"]

print("Testing regions...\n")
for region in regions:
    try:
        cloud = tinytuya.Cloud(
            apiRegion=region,
            apiKey=CLIENT_ID,
            apiSecret=CLIENT_SECRET
        )
        print(f"Testing {region}...")
        devices = cloud.getdevices()
        print(f"  Response: {devices}")
        if devices and len(devices) > 0:
            print(f"✓ SUCCESS: Your region is '{region}'")
            print(f"  Found {len(devices)} devices")
            for d in devices:
                print(f"    - {d.get('name', 'Unknown')}: {d.get('id')}")
            break
        else:
            print(f"  No devices found in {region}")
    except Exception as e:
        print(f"✗ {region}: {e}")

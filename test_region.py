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
        devices = cloud.getdevices()
        if devices:
            print(f"✓ SUCCESS: Your region is '{region}'")
            print(f"  Found {len(devices)} devices")
            break
    except Exception as e:
        print(f"✗ {region}: {str(e)[:50]}")

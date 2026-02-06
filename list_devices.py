#!/home/charoyan/projects/tuya/venv/bin/python3
import json
import tinytuya

# Edit these with your credentials
REGION = "eu"  # or "us", "cn", etc.
CLIENT_ID = "YOUR_CLIENT_ID"
CLIENT_SECRET = "YOUR_CLIENT_SECRET"

cloud = tinytuya.Cloud(
    apiRegion=REGION,
    apiKey=CLIENT_ID,
    apiSecret=CLIENT_SECRET
)

devices = cloud.getdevices()
print("\nYour Tuya devices:\n")
for device in devices:
    print(f"Name: {device['name']}")
    print(f"ID: {device['id']}")
    print(f"Type: {device.get('category', 'unknown')}")
    print("-" * 40)

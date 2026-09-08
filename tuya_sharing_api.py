#!/usr/bin/env python3
"""
Smart Life cloud backend (device sharing).

Uses the official `tuya-device-sharing-sdk`, which authenticates against the
user's Smart Life / Tuya Smart app account via a QR code instead of an IoT Core
developer project. It therefore does not depend on an IoT Core service
subscription -- that subscription expiring is what makes the `tinytuya.Cloud`
path in `tuya_client.py` fail with error 28841002.

One-time setup: `./venv/bin/python3 tuya_auth.py` (see README).
"""
import json
import socket
import time
import uuid
from pathlib import Path

import urllib3.util.connection
from tuya_sharing import CustomerApi, LoginControl, SharingTokenListener
from tuya_sharing.customerapi import CustomerTokenInfo

# App registration of the Home Assistant Tuya integration. The QR login endpoint
# only accepts registered schemas, so we reuse the public one -- same as every
# other third-party client built on this SDK.
CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
SCHEMA = "haauthorize"

SESSION_FILE = Path(__file__).parent / "sharing_token.json"

TEMP_CODES = ["temp_current", "temperature", "va_temperature"]
HUMID_CODES = ["humidity_value", "humidity", "va_humidity"]
BATTERY_CODES = ["battery_state", "battery_percentage"]

BATTERY_MAP = {"high": 80, "middle": 40, "low": 10}


def log(message, log_func):
    if log_func:
        log_func(message)


def prefer_ipv4():
    """
    Make cloud requests use IPv4 only.

    apigw.tuyaeu.com advertises AAAA records that are black-holed on a typical
    dual-stack home connection. urllib3 walks the resolved addresses one by one
    using the SDK's 60s timeout, so a single request spent ~180s on three dead
    IPv6 addresses before falling back to IPv4, which answers in ~0.2s.
    """
    urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET


# --- session storage -------------------------------------------------------

def load_session():
    """Return the saved sharing session, or None if authentication is missing."""
    if not SESSION_FILE.exists():
        return None
    try:
        with open(SESSION_FILE) as f:
            session = json.load(f)
        if session.get("token_info", {}).get("refresh_token"):
            return session
    except Exception:
        pass
    return None


def save_session(session):
    with open(SESSION_FILE, 'w') as f:
        json.dump(session, f, indent=2)
    SESSION_FILE.chmod(0o600)


class _TokenListener(SharingTokenListener):
    """Persists rotated tokens so the next widget refresh reuses them."""

    def __init__(self, session):
        self.session = session

    def update_token(self, token_info):
        self.session["token_info"] = token_info
        save_session(self.session)


def get_api(log_func=None):
    """Build a CustomerApi from the stored session, or None if not logged in."""
    session = load_session()
    if not session:
        log("SHARING: no session, run tuya_auth.py", log_func)
        return None

    prefer_ipv4()

    return CustomerApi(
        CustomerTokenInfo(session["token_info"]),
        CLIENT_ID,
        session["user_code"],
        session["endpoint"],
        _TokenListener(session),
    )


# --- device queries --------------------------------------------------------

def query_devices(api, device_ids, log_func=None):
    """
    Fetch status for the given device ids in a single request.

    Returns a map of device_id -> {"name": str, "status": {code: value}}.

    This calls the detail endpoint directly instead of going through
    `Manager.update_device_cache()`, which would issue four extra enrichment
    requests per device on every widget refresh.
    """
    if not device_ids:
        return {}

    log(f"SHARING API CALL: device detail for {len(device_ids)} devices", log_func)
    response = api.get(
        "/v1.0/m/life/ha/devices/detail",
        {"devIds": ",".join(device_ids)},
    )

    devices = {}
    if response and response.get("success"):
        for item in response.get("result", []):
            status = {}
            # Devices with a broken product definition return status: null
            for entry in item.get("status") or []:
                if "code" in entry and "value" in entry:
                    status[entry["code"]] = entry["value"]
            devices[item["id"]] = {"name": item.get("name", ""), "status": status}

    return devices


def query_shadow(api, device_id, log_func=None):
    """
    Fetch last reported properties for a single device.

    Needed because some sensors have a broken product definition in Tuya's
    catalog (category `tdq` instead of `wsdcg`, empty specifications), so the
    device detail endpoint returns `status: null` for them. The shadow endpoint
    reports raw properties regardless of the product spec. This mirrors the
    shadow fallback the IoT Core path uses for the same devices.
    """
    log(f"SHARING API CALL: shadow properties for {device_id[:8]} (fallback)", log_func)
    response = api.get(f"/v1.0/m/life/ha/{device_id}/shadow/properties")

    status = {}
    if response and response.get("success"):
        for prop in response.get("result", {}).get("properties", []):
            if "code" in prop and "value" in prop:
                status[prop["code"]] = prop["value"]

    return status


def _local_name(config, device_id):
    """Name from local config, which takes priority over the cloud name."""
    for device_conf in config.get("local_devices", []):
        if device_conf.get("id") == device_id:
            return device_conf.get("name")
    for socket_conf in config.get("local_sockets", []):
        if socket_conf.get("id") == device_id:
            return socket_conf.get("name")
    if config.get("local_socket", {}).get("id") == device_id:
        return config["local_socket"].get("name")
    return None


def _parse_thermometer(status):
    """Extract (temperature, humidity, battery) from a status code map."""
    temp = None
    humid = None
    battery = None

    for code, value in status.items():
        if code in TEMP_CODES and isinstance(value, (int, float)):
            temp = value / 10 if value > 100 else value
        elif code in HUMID_CODES and isinstance(value, (int, float)):
            humid = value
        elif code in BATTERY_CODES:
            battery = value

    return temp, humid, battery


def _battery_percent(battery):
    if isinstance(battery, str):
        return BATTERY_MAP.get(battery.lower(), 50)
    if isinstance(battery, (int, float)):
        return int(battery)
    return 100


def get_sharing_temperatures(config, log_func=None):
    """Thermometer data via the Smart Life cloud. Same shape as the other backends."""
    api = get_api(log_func)
    devices = config.get("devices", [])

    temps = []
    humids = []
    names = []
    batteries = []

    device_data = query_devices(api, devices, log_func) if api else {}

    for device_id in devices:
        data = device_data.get(device_id, {})
        temp, humid, battery = _parse_thermometer(data.get("status") or {})

        if temp is None and api:
            temp, humid, battery = _parse_thermometer(
                query_shadow(api, device_id, log_func)
            )

        temps.append(f"{temp:.1f}" if temp is not None else "--")
        humids.append(f"{humid}" if humid is not None else "--")
        names.append(_local_name(config, device_id) or data.get("name") or device_id[:8])
        batteries.append(_battery_percent(battery))

    while len(temps) < 3:
        temps.append("-")
        humids.append("-")
        names.append("")
        batteries.append(0)

    return {
        "temperatures": temps,
        "humidity": humids,
        "names": names,
        "batteries": batteries,
    }


def _parse_socket(status):
    result = {"power": "--", "voltage": "--", "energy": "--"}

    for code, value in status.items():
        if not isinstance(value, (int, float)):
            continue
        if code == "cur_power":
            result["power"] = f"{value / 10:.1f}"
        elif code == "cur_voltage":
            result["voltage"] = f"{value / 10:.0f}"
        elif code == "add_ele":
            result["energy"] = f"{value / 1000:.2f}"

    return result


def get_sharing_socket_data(config, log_func=None):
    """Smart plug data via the Smart Life cloud."""
    socket_ids = [s["id"] for s in config.get("local_sockets", [])]
    if not socket_ids and config.get("local_socket"):
        socket_ids = [config["local_socket"]["id"]]
    if not socket_ids and config.get("socket"):
        socket_ids = [config["socket"]]

    api = get_api(log_func)
    if not api or not socket_ids:
        return {"sockets": []}

    device_data = query_devices(api, socket_ids, log_func)

    result = []
    for socket_id in socket_ids:
        data = device_data.get(socket_id, {})
        socket_data = _parse_socket(data.get("status", {}))
        socket_data["name"] = _local_name(config, socket_id) or data.get("name") or "Socket"
        socket_data["id"] = socket_id
        result.append(socket_data)

    if len(result) == 1:
        return {"socket": result[0], "sockets": result}

    return {"sockets": result}


# --- authentication --------------------------------------------------------

def request_qr(user_code):
    """Start a QR login. Returns the QR token to be encoded and scanned."""
    prefer_ipv4()
    control = LoginControl()
    response = control.qr_code(CLIENT_ID, SCHEMA, user_code)
    if not response.get("success"):
        raise RuntimeError(
            f"QR request rejected: {response.get('msg')} (code {response.get('code')})"
        )
    return control, response["result"]["qrcode"]


def qr_payload(qr_token):
    """Content to encode in the QR image for the Smart Life app scanner."""
    return f"tuyaSmart--qrLogin?token={qr_token}"


def await_login(control, qr_token, user_code, timeout=180, interval=3):
    """
    Poll until the QR code is confirmed in the app, then store the session.

    Returns the saved session dict, or raises on timeout.
    """
    deadline = time.time() + timeout
    last_error = None

    while time.time() < deadline:
        ok, info = control.login_result(qr_token, CLIENT_ID, user_code)
        if ok:
            session = {
                "user_code": user_code,
                "endpoint": info["endpoint"],
                "terminal_id": info.get("terminal_id") or uuid.uuid4().hex,
                "token_info": {
                    "t": info["t"],
                    "uid": info["uid"],
                    "expire_time": info["expire_time"],
                    "access_token": info["access_token"],
                    "refresh_token": info["refresh_token"],
                },
            }
            save_session(session)
            return session

        last_error = info.get("msg")
        time.sleep(interval)

    raise TimeoutError(f"QR code was not confirmed in time (last status: {last_error})")

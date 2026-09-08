#!/usr/bin/env python3
"""
Chart history for the widget: from the collector server if it answers,
from the local file if it does not.

The fallback is what makes the server optional. With no server configured, or
with the network down, the widget behaves exactly as it did before.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from tuya_history import get_device_history


def local_history(device_id, hours):
    """Separate function so tests can replace it."""
    return get_device_history(device_id, hours)


def fetch_remote(base_url, token, device_id, hours, timeout):
    """[[ts, power, voltage], ...] from the server, or None if it did not answer."""
    query = urllib.parse.urlencode({"device": device_id, "hours": hours})
    request = urllib.request.Request(f"{base_url.rstrip('/')}/history?{query}")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception:
        return None


def get_history(settings_dict, token, device_id, hours):
    """
    (rows, source) where source is "server" or "local".

    The source is returned so the widget can say which one it drew, instead of
    silently showing a gappy local chart that looks like real data.
    """
    base_url = settings_dict.get("history_server") or ""
    if base_url:
        rows = fetch_remote(
            base_url, token, device_id, hours,
            settings_dict.get("history_timeout", 3),
        )
        if rows:
            return rows, "server"
        if rows is not None:
            # The server answered with nothing. That happens right after
            # deployment, before the collector has rows for this device. Prefer
            # local data if there is any, rather than drawing an empty chart.
            local = local_history(device_id, hours)
            return (local, "local") if local else ([], "server")

    return local_history(device_id, hours), "local"

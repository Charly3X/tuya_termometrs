#!/usr/bin/env python3
"""
Behaviour settings, kept apart from secrets.

`config.json` holds credentials, device ids and tokens and is never committed.
This file holds everything else: retention, intervals, ports, addresses. Both
machines use the same filename with different contents.
"""
import copy
import json
from pathlib import Path

SETTINGS_FILE = Path(__file__).parent / "settings.json"

DEFAULTS = {
    "region": "eu",
    "cloud_backend": "sharing",
    "history_server": "",
    "history_timeout": 3,
    "server": {
        "database": "readings.db",
        "port": 8080,
        "retention_days": 365,
        # Shadow-polled sensors were measured reporting every 20-45 minutes,
        # so a shorter interval buys no extra resolution -- only API calls.
        "poll_interval": 900,
    },
}


def _merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(path=None):
    """Settings from disk merged over DEFAULTS. Missing file means all defaults."""
    path = Path(path) if path else SETTINGS_FILE
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    with open(path) as f:
        return _merge(DEFAULTS, json.load(f))

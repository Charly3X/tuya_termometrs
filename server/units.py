#!/usr/bin/env python3
"""
Tuya status codes to metric names and human units.

Tuya sends integers in its own scales: cur_power 909 means 90.9 W. The scale
is declared per code in the product specification, so we ask for it rather
than hardcoding. FALLBACK_SCALES covers devices whose specification comes back
empty because their product definition is broken in Tuya's catalog.
"""

METRIC_NAMES = {
    "cur_power": "power",
    "cur_voltage": "voltage",
    "cur_current": "current",
    "add_ele": "energy",
    "va_temperature": "temperature",
    "temp_current": "temperature",
    "va_humidity": "humidity",
    "humidity_value": "humidity",
    "battery_state": "battery",
    "battery_percentage": "battery",
}

FALLBACK_SCALES = {
    "cur_power": 1,
    "cur_voltage": 1,
    "cur_current": 0,
    "add_ele": 2,
    "va_temperature": 1,
    "temp_current": 1,
    "va_humidity": 0,
    "humidity_value": 0,
    "battery_percentage": 0,
}

# Codes where the hardware contradicts its own declared specification.
# These win over whatever Tuya reports, so do not "simplify" them away.
SCALE_OVERRIDES = {
    # Tuya declares add_ele as {"scale": 3, "step": 100}, which would mean
    # 0.001 kWh units arriving in multiples of 100. The plugs report 9 and 45.
    # Measured 2026-09-08: the local DPS 20 value and the cloud add_ele value
    # are the same number, and 45 -> 0.45 kWh matches a fridge drawing ~69 W
    # for ten hours at roughly half duty. 0.045 kWh would be 40 minutes of
    # running in ten hours, which is impossible for a fridge that is on.
    "add_ele": 2,
}

BATTERY_STATE = {"low": 10.0, "middle": 40.0, "high": 80.0}


def convert(code, value, scales):
    """
    (metric_name, value_in_human_units), or None if the code is not tracked
    or the value is not numeric.
    """
    metric = METRIC_NAMES.get(code)
    if metric is None:
        return None

    if isinstance(value, str):
        if code == "battery_state":
            return metric, BATTERY_STATE.get(value.lower(), 50.0)
        return None

    # bool is a subclass of int, and switch_1 must not become a number
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    if code in SCALE_OVERRIDES:
        scale = SCALE_OVERRIDES[code]
    else:
        scale = scales.get(code, FALLBACK_SCALES.get(code, 0))
    return metric, value / (10 ** scale)

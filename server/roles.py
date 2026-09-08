#!/usr/bin/env python3
"""
Decide how each device is read, by asking Tuya rather than by configuration.

A device whose dpId map is populated can be decoded from the MQTT push. A
device with an empty map cannot — its product definition is broken in Tuya's
catalog — so it has to be polled through the shadow endpoint instead.

Deciding this at startup means the split survives Tuya fixing or breaking a
product definition: nobody has to remember to edit a config file.
"""
import json

from server.units import FALLBACK_SCALES


def device_profile(api, device_id):
    """{"push": bool, "scales": {code: scale}} for one device."""
    push = False
    try:
        response = api.get(f"/v1.0/m/life/devices/{device_id}/status")
        relations = (response or {}).get("result", {}).get("dpStatusRelationDTOS")
        push = bool(relations)
    except Exception:
        push = False

    scales = dict(FALLBACK_SCALES)
    try:
        response = api.get(f"/v1.1/m/life/{device_id}/specifications")
        for item in (response or {}).get("result", {}).get("status", []):
            values = json.loads(item.get("values") or "{}")
            if "scale" in values:
                scales[item["code"]] = int(values["scale"])
    except Exception:
        pass

    return {"push": push, "scales": scales}


def classify(api, device_ids):
    """(push_ids, poll_ids, {device_id: scales})."""
    push, poll, scales = [], [], {}
    for device_id in device_ids:
        profile = device_profile(api, device_id)
        scales[device_id] = profile["scales"]
        (push if profile["push"] else poll).append(device_id)
    return push, poll, scales

#!/usr/bin/env python3
"""
The collector service.

Devices that can be decoded from Tuya's MQTT push are recorded the moment a
report arrives — there is no polling interval to tune, and the resolution is
whatever the device actually sends. Devices whose product definition is broken
in Tuya's catalog cannot be decoded from the push, so they are polled through
the shadow endpoint instead.
"""
import logging
import threading
import time

import tuya_sharing_api
from server import roles, storage
from server.units import convert
from tuya_sharing import Manager, SharingDeviceListener
from tuya_sharing.mq import SharingMQ

log = logging.getLogger("collector")


def record(conn, device_id, status, scales, ts):
    """Convert a {code: value} report and store it. Returns rows written."""
    values = {}
    for code, raw in status.items():
        converted = convert(code, raw, scales)
        if converted:
            metric, value = converted
            values[metric] = value
    return storage.write(conn, ts, device_id, values)


def record_shadow(conn, device_id, properties, scales):
    """
    Store shadow properties, each at the time the device reported it.

    Shadow carries a per-property millisecond timestamp, which is more honest
    than stamping everything with the poll time: these sensors report minutes
    apart from each other.
    """
    written = 0
    for prop in properties:
        if "time" not in prop or "code" not in prop or "value" not in prop:
            continue
        converted = convert(prop["code"], prop["value"], scales)
        if not converted:
            continue
        metric, value = converted
        written += storage.write(
            conn, int(prop["time"] // 1000), device_id, {metric: value}
        )
    return written


class _PushListener(SharingDeviceListener):
    def __init__(self, database, scales):
        self.database = database
        self.scales = scales
        self._local = threading.local()

    def _conn(self):
        """
        Open (once) and reuse a connection private to the calling thread.

        paho-mqtt dispatches update_device() on its own network-loop thread,
        which is not the thread that constructed this listener. A sqlite3
        connection is bound to the thread that created it -- using one from
        another thread raises ProgrammingError -- so sharing a single
        connection between the main thread and the MQTT thread silently loses
        every push write. check_same_thread=False would silence that error
        without removing the hazard: two threads would then interleave writes
        on one connection. The actual fix is a second, separate connection,
        opened lazily on whichever thread first calls in here and cached on
        it from then on. Two writers against one database is exactly the case
        WAL mode -- already enabled by storage.connect() -- exists to support.
        Do not "simplify" this back to a single shared connection.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = storage.connect(self.database)
            self._local.conn = conn
        return conn

    def update_device(self, device, updated_status_properties=None, dp_timestamps=None):
        if not updated_status_properties:
            return
        status = {c: device.status.get(c) for c in updated_status_properties}
        scales = self.scales.get(device.id, {})
        try:
            record(self._conn(), device.id, status, scales, int(time.time()))
        except Exception as e:
            log.error("failed to record push from %s: %s", device.id, e)

    def add_device(self, device):
        pass

    def remove_device(self, device_id):
        pass


def start_push(manager, database, scales):
    """
    Subscribe to device topics directly.

    Manager.refresh_mq() cannot be used: it only subscribes to a device topic
    when device.set_up is true, and no device on this account has that flag,
    so it delivers nothing. Measured: 0 events in 90s through refresh_mq,
    27 events in 90s through this path.
    """
    manager.add_device_listener(_PushListener(database, scales))
    mq = SharingMQ(
        manager.customer_api,
        [home.id for home in manager.user_homes],
        list(manager.device_map.values()),
    )
    mq.start()
    mq.add_message_listener(manager.on_message)
    manager.mq = mq
    return mq


def poll_once(api, conn, device_ids, scales):
    """One shadow poll for every device that cannot be read from the push."""
    for device_id in device_ids:
        try:
            response = api.get(f"/v1.0/m/life/ha/{device_id}/shadow/properties")
            properties = (response or {}).get("result", {}).get("properties", [])
            record_shadow(conn, device_id, properties, scales.get(device_id, {}))
        except Exception as e:
            log.error("shadow poll failed for %s: %s", device_id, e)


def run(settings_dict, device_ids):
    """Service entry point. Runs until killed."""
    tuya_sharing_api.prefer_ipv4()
    session = tuya_sharing_api.load_session()
    if not session:
        raise SystemExit("No Smart Life session. Run tuya_auth.py on this machine.")

    database = settings_dict["server"]["database"]
    conn = storage.connect(database)
    api = tuya_sharing_api.get_api()

    push_ids, poll_ids, scales = roles.classify(api, device_ids)
    log.info("push: %s", push_ids)
    log.info("poll: %s", poll_ids)

    manager = Manager(
        tuya_sharing_api.CLIENT_ID,
        session["user_code"],
        session["terminal_id"],
        session["endpoint"],
        session["token_info"],
        tuya_sharing_api._TokenListener(session),
    )
    manager.update_device_cache()
    start_push(manager, database, scales)

    interval = settings_dict["server"]["poll_interval"]
    while True:
        poll_once(api, conn, poll_ids, scales)
        time.sleep(interval)


if __name__ == "__main__":
    import json
    from pathlib import Path

    from settings import load_settings

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    config = json.loads((Path(__file__).parent.parent / "config.json").read_text())
    devices = list(config.get("devices", []))
    devices += [s["id"] for s in config.get("local_sockets", [])]
    run(load_settings(), devices)

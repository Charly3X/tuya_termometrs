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

# How often a heartbeat "still the same" row is written for a metric that has
# gone quiet, and which metrics get one.
#
# Every charted quantity needs this, because every one of them can sit
# unchanged for a long time and Tuya only sends changes. Power holds a
# constant 0 whenever a compressor stops or nothing is plugged in. The
# thermometers are worse: they only report past a threshold of roughly half a
# degree, so measured over a real day they produced four or five readings each
# -- an hour-long chart was simply empty and a day-long one was a four-segment
# zigzag.
#
# Battery is left out on purpose: it is not charted, and it moves so slowly
# that a row a minute would be pure landfill.
HEARTBEAT_SECONDS = 60
HEARTBEAT_METRICS = ("power", "temperature", "humidity")

# Guards every read-modify-write of a `last_values` map shared between the
# MQTT callback thread (_PushListener, via record()) and the main thread
# (heartbeat(), record_shadow() during polling). Dict item assignment and
# .get() are individually atomic under CPython's GIL, but heartbeat's
# "read the last value, decide whether it is stale, then refresh its
# timestamp" is three separate operations -- without a lock a push landing
# in the middle of that sequence could be silently overwritten or read
# half-updated. The lock makes that sequence atomic instead of relying on
# an interpreter implementation detail.
_last_values_lock = threading.Lock()


def _remember(last_values, device_id, metric, ts, value):
    if last_values is None:
        return
    with _last_values_lock:
        last_values[(device_id, metric)] = [ts, value]


def record(conn, device_id, status, scales, ts, last_values=None):
    """Convert a {code: value} report and store it. Returns rows written."""
    values = {}
    for code, raw in status.items():
        converted = convert(code, raw, scales)
        if converted:
            metric, value = converted
            values[metric] = value
    written = storage.write(conn, ts, device_id, values)
    for metric, value in values.items():
        _remember(last_values, device_id, metric, ts, value)
    return written


def record_shadow(conn, device_id, properties, scales, seen=None, last_values=None):
    """
    Store shadow properties, each at the time the device reported it.

    Shadow carries a per-property millisecond timestamp, which is more honest
    than stamping everything with the poll time: these sensors report minutes
    apart from each other.

    `seen` is a {(device, metric): last_ts} map the caller carries across
    polls. The shadow endpoint keeps returning the last reported value
    forever, so without it every unchanged reading is re-inserted on every
    poll: measured, these sensors report every 20-45 minutes, so each
    reading lands again and again until the device finally reports a new
    one. A property whose timestamp has not advanced is a reading we
    already stored, so it is skipped. Deliberately not enforced with a
    UNIQUE index -- that would change storage.write() for the push path too,
    where two identical readings at different times are legitimate data.
    """
    written = 0
    for prop in properties:
        if "time" not in prop or "code" not in prop or "value" not in prop:
            continue
        converted = convert(prop["code"], prop["value"], scales)
        if not converted:
            continue
        metric, value = converted
        ts = int(prop["time"] // 1000)
        if seen is not None:
            key = (device_id, metric)
            if key in seen and ts <= seen[key]:
                continue
            seen[key] = ts
        written += storage.write(conn, ts, device_id, {metric: value})
        _remember(last_values, device_id, metric, ts, value)
    return written


def heartbeat(conn, last_values, online, now):
    """
    Write one "still the same" row per heartbeat metric that has gone quiet,
    for devices that are currently online.

    A push protocol only sends changes, so silence from an ONLINE device is
    a sound basis for "unchanged" -- but silence from an OFFLINE (or unknown)
    device means "unknown", and writing anything then would be a lie. Devices
    missing from `online`, or mapped to False, are therefore skipped
    entirely, even if their last value is stale. The gate checks `is True`
    rather than truthiness: `online` is built from whatever a caller passes,
    and a non-boolean value (a stray string, an int) must not be mistaken
    for "online" either.

    `last_values` is the same {(device_id, metric): [ts, value]} map that
    record() and record_shadow() populate, mutated in place here too so the
    heartbeat clock resets once a row is written -- otherwise every
    subsequent tick within the hour would heartbeat again.

    Returns the number of rows written.
    """
    written = 0
    with _last_values_lock:
        for device_id, is_online in online.items():
            if is_online is not True:
                continue
            for metric in HEARTBEAT_METRICS:
                entry = last_values.get((device_id, metric))
                if entry is None:
                    continue
                ts, value = entry
                if now - ts < HEARTBEAT_SECONDS:
                    continue
                written += storage.write(conn, now, device_id, {metric: value})
                entry[0] = now
    return written


class _PushListener(SharingDeviceListener):
    def __init__(self, database, scales, last_values=None):
        self.database = database
        self.scales = scales
        self.last_values = last_values
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
            # record() takes _last_values_lock internally before touching
            # self.last_values, which heartbeat() (main thread) reads and
            # updates under the same lock -- see _remember()'s docstring.
            # The sqlite connection stays per-thread as above; only the
            # last_values dict is actually shared, and only through the lock.
            record(self._conn(), device.id, status, scales, int(time.time()), self.last_values)
        except Exception as e:
            log.error("failed to record push from %s: %s", device.id, e)

    def add_device(self, device):
        pass

    def remove_device(self, device_id):
        pass


def start_push(manager, database, scales, last_values=None):
    """
    Subscribe to device topics directly.

    Manager.refresh_mq() cannot be used: it only subscribes to a device topic
    when device.set_up is true, and no device on this account has that flag,
    so it delivers nothing. Measured: 0 events in 90s through refresh_mq,
    27 events in 90s through this path.
    """
    manager.add_device_listener(_PushListener(database, scales, last_values))
    mq = SharingMQ(
        manager.customer_api,
        [home.id for home in manager.user_homes],
        list(manager.device_map.values()),
    )
    mq.start()
    mq.add_message_listener(manager.on_message)
    manager.mq = mq
    return mq


def poll_once(api, conn, device_ids, scales, seen=None, last_values=None):
    """
    One shadow poll for every device that cannot be read from the push.

    Pass the same `seen` map on every call so unchanged readings are not
    stored again -- see record_shadow().
    """
    for device_id in device_ids:
        try:
            response = api.get(f"/v1.0/m/life/ha/{device_id}/shadow/properties")
            properties = (response or {}).get("result", {}).get("properties", [])
            record_shadow(conn, device_id, properties, scales.get(device_id, {}), seen, last_values)
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

    # Exactly one Tuya session for the whole process. Manager builds its own
    # CustomerApi internally, so calling tuya_sharing_api.get_api() as well
    # would create a SECOND holder of the same refresh token, each with its
    # own token listener. Every request is signed with the current refresh
    # token and Tuya invalidates the old one on rotation, so whichever
    # instance refreshed first would orphan the other -- and the SDK swallows
    # the resulting failures ("net work error"). If the Manager's instance
    # lost that race, SharingMQ could not renew its MQTT config at the ~2h
    # mark and push would stop permanently, while shadow polling kept
    # working: a service that looks alive, so Restart=always never fires.
    # Everything that talks to Tuya goes through manager.customer_api.
    # tests/test_collector.py::test_run_builds_exactly_one_tuya_session
    # enforces this: it fails tuya_sharing_api.get_api() outright and checks
    # that classify()/poll_once() are handed manager.customer_api.
    manager = Manager(
        tuya_sharing_api.CLIENT_ID,
        session["user_code"],
        session["terminal_id"],
        session["endpoint"],
        session["token_info"],
        tuya_sharing_api._TokenListener(session),
    )
    api = manager.customer_api

    push_ids, poll_ids, scales = roles.classify(api, device_ids)
    log.info("push: %s", push_ids)
    log.info("poll: %s", poll_ids)

    manager.update_device_cache()
    last_values = {}
    start_push(manager, database, scales, last_values)

    # The shadow poll stays on its own, much longer poll_interval (900s by
    # default) -- these sensors were measured reporting every 20-45 minutes,
    # so polling faster buys no resolution, only extra API calls. The loop
    # itself now ticks every HEARTBEAT_SECONDS so the heartbeat can run on
    # its own, tighter cadence without changing that.
    interval = settings_dict["server"]["poll_interval"]
    seen = {}
    last_poll = None
    while True:
        now = int(time.time())
        try:
            # Read online state straight from the SDK's own bookkeeping
            # rather than tracking it ourselves -- Manager already updates
            # device_map[id].online from MQTT bizCode online/offline events.
            # CustomerDevice is a SimpleNamespace built from whatever keys
            # Tuya returned, so `.online` is read defensively: missing
            # entirely -> not online, and anything that isn't literally
            # True (a stray string, an int) -> not online too, matching
            # heartbeat()'s own strict `is True` gate.
            online = {}
            for device_id, device in manager.device_map.items():
                flag = getattr(device, "online", False)
                online[device_id] = flag is True
            heartbeat(conn, last_values, online, now)
        except Exception as e:
            # A failed heartbeat tick must cost one flat-line minute, not
            # the whole collector -- record()/record_shadow()/poll_once()/
            # _PushListener.update_device() all catch-and-log per item for
            # the same reason. storage.write() can raise on a sqlite busy
            # timeout (plausible while the nightly VACUUM holds an
            # exclusive lock), and this is the one call site that wasn't
            # already guarded.
            log.error("heartbeat failed: %s", e)
        if last_poll is None or now - last_poll >= interval:
            poll_once(api, conn, poll_ids, scales, seen, last_values)
            last_poll = now
        time.sleep(HEARTBEAT_SECONDS)


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

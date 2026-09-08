import threading

import pytest
import tuya_sharing_api
from server import collector, storage

SCALES = {"cur_power": 1, "cur_voltage": 1, "add_ele": 3, "temp_current": 1}


class _FakeDevice:
    """Just enough of tuya_sharing's CustomerDevice for _PushListener."""

    def __init__(self, device_id, status):
        self.id = device_id
        self.status = status


@pytest.fixture
def conn(tmp_path):
    c = storage.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_record_writes_converted_values(conn):
    collector.record(conn, "dev1", {"cur_power": 909, "cur_voltage": 2365}, SCALES, 100)
    assert storage.series(conn, "dev1", "power", 0) == [(100, 90.9)]
    assert storage.series(conn, "dev1", "voltage", 0) == [(100, 236.5)]


def test_partial_report_writes_only_what_arrived(conn):
    collector.record(conn, "dev1", {"cur_power": 909, "cur_voltage": 2365}, SCALES, 100)
    collector.record(conn, "dev1", {"cur_power": 800}, SCALES, 110)
    assert storage.series(conn, "dev1", "power", 0) == [(100, 90.9), (110, 80.0)]
    # the earlier voltage must survive, not be overwritten or duplicated
    assert storage.series(conn, "dev1", "voltage", 0) == [(100, 236.5)]


def test_untracked_codes_are_skipped(conn):
    written = collector.record(
        conn, "dev1", {"cur_power": 909, "switch_1": True, "relay_status": "2"}, SCALES, 100
    )
    assert written == 1


def test_empty_report_writes_nothing(conn):
    assert collector.record(conn, "dev1", {}, SCALES, 100) == 0


def test_shadow_properties_are_recorded_with_their_own_timestamps(conn):
    properties = [
        {"code": "temp_current", "value": 244, "time": 1788844610734},
        {"code": "humidity_value", "value": 51, "time": 1788844610734},
    ]
    collector.record_shadow(conn, "dev2", properties, SCALES)
    assert storage.series(conn, "dev2", "temperature", 0) == [(1788844610, 24.4)]
    assert storage.series(conn, "dev2", "humidity", 0) == [(1788844610, 51.0)]


def test_shadow_property_without_a_timestamp_is_skipped(conn):
    collector.record_shadow(conn, "dev2", [{"code": "temp_current", "value": 244}], SCALES)
    assert storage.series(conn, "dev2", "temperature", 0) == []


class _FakeShadowApi:
    """Returns the same shadow answer every time, exactly like Tuya does."""

    def __init__(self, properties):
        self.properties = properties
        self.calls = 0

    def get(self, path):
        self.calls += 1
        return {"result": {"properties": self.properties}}


def test_second_poll_of_unchanged_shadow_data_writes_nothing(conn):
    # The shadow endpoint keeps returning the last reported value forever.
    # These sensors report every 20-45 minutes, so most polls see nothing
    # new and must not re-insert what is already stored.
    api = _FakeShadowApi([
        {"code": "temp_current", "value": 244, "time": 1788844610734},
        {"code": "humidity_value", "value": 51, "time": 1788844610734},
    ])
    seen = {}
    collector.poll_once(api, conn, ["dev2"], {"dev2": SCALES}, seen)
    collector.poll_once(api, conn, ["dev2"], {"dev2": SCALES}, seen)
    collector.poll_once(api, conn, ["dev2"], {"dev2": SCALES}, seen)

    assert api.calls == 3  # the polls really happened
    assert storage.series(conn, "dev2", "temperature", 0) == [(1788844610, 24.4)]
    assert storage.series(conn, "dev2", "humidity", 0) == [(1788844610, 51.0)]


def test_poll_records_a_reading_once_its_timestamp_advances(conn):
    api = _FakeShadowApi([{"code": "temp_current", "value": 244, "time": 1788844610734}])
    seen = {}
    collector.poll_once(api, conn, ["dev2"], {"dev2": SCALES}, seen)
    api.properties = [{"code": "temp_current", "value": 251, "time": 1788846610734}]
    collector.poll_once(api, conn, ["dev2"], {"dev2": SCALES}, seen)

    assert storage.series(conn, "dev2", "temperature", 0) == [
        (1788844610, 24.4),
        (1788846610, 25.1),
    ]


def test_shadow_dedup_is_tracked_per_device_and_metric(conn):
    # Two devices reporting the same metric at the same instant are two
    # separate readings; one must not mask the other.
    api = _FakeShadowApi([{"code": "temp_current", "value": 244, "time": 1788844610734}])
    seen = {}
    collector.poll_once(api, conn, ["dev2", "dev3"], {"dev2": SCALES, "dev3": SCALES}, seen)

    assert storage.series(conn, "dev2", "temperature", 0) == [(1788844610, 24.4)]
    assert storage.series(conn, "dev3", "temperature", 0) == [(1788844610, 24.4)]


def test_heartbeat_writes_a_row_for_a_stale_metric_of_an_online_device(conn):
    last_values = {("dev1", "power"): [100, 42.0]}
    online = {"dev1": True}
    written = collector.heartbeat(conn, last_values, online, 100 + collector.HEARTBEAT_SECONDS)
    assert written == 1
    assert storage.series(conn, "dev1", "power", 0) == [
        (100 + collector.HEARTBEAT_SECONDS, 42.0)
    ]


def test_heartbeat_leaves_a_recently_written_metric_alone(conn):
    last_values = {("dev1", "power"): [100, 42.0]}
    online = {"dev1": True}
    written = collector.heartbeat(conn, last_values, online, 100 + collector.HEARTBEAT_SECONDS - 1)
    assert written == 0
    assert storage.series(conn, "dev1", "power", 0) == []


def test_heartbeat_skips_an_offline_device(conn):
    last_values = {("dev1", "power"): [100, 42.0]}
    online = {"dev1": False}
    written = collector.heartbeat(conn, last_values, online, 100 + collector.HEARTBEAT_SECONDS)
    assert written == 0
    assert storage.series(conn, "dev1", "power", 0) == []


def test_heartbeat_skips_a_device_absent_from_online(conn):
    last_values = {("dev1", "power"): [100, 42.0]}
    online = {}
    written = collector.heartbeat(conn, last_values, online, 100 + collector.HEARTBEAT_SECONDS)
    assert written == 0
    assert storage.series(conn, "dev1", "power", 0) == []


def test_heartbeat_never_writes_a_non_heartbeat_metric(conn):
    last_values = {("dev1", "temperature"): [100, 21.0]}
    online = {"dev1": True}
    written = collector.heartbeat(conn, last_values, online, 100 + collector.HEARTBEAT_SECONDS)
    assert written == 0
    assert storage.series(conn, "dev1", "temperature", 0) == []


def test_second_heartbeat_immediately_after_the_first_writes_nothing(conn):
    last_values = {("dev1", "power"): [100, 42.0]}
    online = {"dev1": True}
    now = 100 + collector.HEARTBEAT_SECONDS
    assert collector.heartbeat(conn, last_values, online, now) == 1
    assert collector.heartbeat(conn, last_values, online, now) == 0
    assert storage.series(conn, "dev1", "power", 0) == [(now, 42.0)]


def test_record_populates_last_values_so_a_push_resets_the_clock(conn):
    last_values = {}
    collector.record(conn, "dev1", {"cur_power": 909}, SCALES, 100, last_values)
    assert last_values[("dev1", "power")] == [100, 90.9]

    online = {"dev1": True}
    # Not yet stale relative to the push's own timestamp.
    assert collector.heartbeat(conn, last_values, online, 100 + collector.HEARTBEAT_SECONDS - 1) == 0
    # Stale now.
    assert collector.heartbeat(conn, last_values, online, 100 + collector.HEARTBEAT_SECONDS) == 1
    assert storage.series(conn, "dev1", "power", 0) == [
        (100, 90.9),
        (100 + collector.HEARTBEAT_SECONDS, 90.9),
    ]


def test_push_listener_writes_from_the_mqtt_callback_thread(tmp_path):
    """
    Reproduces the real bug: paho-mqtt calls update_device() on its own
    thread, not the thread that built the listener. A _PushListener backed by
    a single connection created up front would hand that connection to the
    callback thread and sqlite3 would refuse it (ProgrammingError, silently
    swallowed by the listener's own try/except), so nothing would be written.
    The fix is a connection opened lazily per-thread, so this must pass.
    """
    db_path = tmp_path / "push.db"
    listener = collector._PushListener(str(db_path), {"dev1": SCALES})
    device = _FakeDevice("dev1", {"cur_power": 909})

    # Deliberately never touch the listener from this (the main) thread first --
    # the very first call must come from a different thread, same as production.
    worker = threading.Thread(
        target=listener.update_device,
        args=(device,),
        kwargs={"updated_status_properties": ["cur_power"]},
    )
    worker.start()
    worker.join()

    # Read back with a fresh connection from the main thread.
    conn = storage.connect(db_path)
    rows = storage.series(conn, "dev1", "power", 0)
    assert len(rows) == 1
    assert rows[0][1] == 90.9


class _LoopBroken(Exception):
    """Raised by the stubbed time.sleep to escape run()'s infinite loop."""


FAKE_SESSION = {
    "user_code": "user1",
    "terminal_id": "term1",
    "endpoint": "https://example.com",
    "token_info": {
        "t": 1, "uid": "u1", "expire_time": 1,
        "access_token": "a", "refresh_token": "r",
    },
}


def test_run_builds_exactly_one_tuya_session(monkeypatch, tmp_path):
    """
    Regression test for the collector's most serious bug: run() used to build
    a second, independent CustomerApi via tuya_sharing_api.get_api() on top of
    the one already inside Manager. Both sign requests with the current
    refresh token, so the first rotation orphaned one of them and the SDK
    swallowed the failure -- MQTT push died permanently about two hours after
    every start while the service still looked alive.

    tuya_sharing_api.get_api is patched to raise if it is ever called, which
    asserts against the real run() code rather than a mock standing in for
    it. classify() and poll_once() are patched only to record which `api`
    object they were handed; the fix is confirmed by checking that object
    `is` manager.customer_api, not some second instance.
    """
    order = []
    get_api_calls = []
    classify_calls = []
    poll_once_calls = []
    start_push_calls = []
    manager_holder = {}

    monkeypatch.setattr(tuya_sharing_api, "load_session", lambda: FAKE_SESSION)

    def fake_prefer_ipv4():
        order.append("prefer_ipv4")

    monkeypatch.setattr(tuya_sharing_api, "prefer_ipv4", fake_prefer_ipv4)

    def fake_get_api(*args, **kwargs):
        get_api_calls.append(1)
        raise AssertionError("second session")

    monkeypatch.setattr(tuya_sharing_api, "get_api", fake_get_api)

    class _FakeManager:
        def __init__(self, client_id, user_code, terminal_id, endpoint, token_info, token_listener):
            order.append("manager_init")
            self.customer_api = object()
            self.user_homes = []
            self.device_map = {}
            manager_holder["instance"] = self

        def update_device_cache(self):
            pass

    monkeypatch.setattr(collector, "Manager", _FakeManager)

    def fake_classify(api, device_ids):
        classify_calls.append(api)
        return list(device_ids), list(device_ids), {d: {} for d in device_ids}

    monkeypatch.setattr(collector.roles, "classify", fake_classify)

    def fake_start_push(manager, database, scales, last_values=None):
        start_push_calls.append(manager)

    monkeypatch.setattr(collector, "start_push", fake_start_push)

    def fake_poll_once(api, conn, device_ids, scales, seen=None, last_values=None):
        poll_once_calls.append(api)

    monkeypatch.setattr(collector, "poll_once", fake_poll_once)

    def fake_sleep(seconds):
        raise _LoopBroken()

    monkeypatch.setattr(collector.time, "sleep", fake_sleep)

    settings_dict = {
        "server": {"database": str(tmp_path / "collector.db"), "poll_interval": 5},
    }

    with pytest.raises(_LoopBroken):
        collector.run(settings_dict, ["dev1"])

    assert get_api_calls == []  # the second session was never built
    manager = manager_holder["instance"]
    assert classify_calls == [manager.customer_api]
    assert poll_once_calls == [manager.customer_api]
    assert start_push_calls == [manager]
    # prefer_ipv4() must run before the network session (Manager) is built.
    assert order == ["prefer_ipv4", "manager_init"]

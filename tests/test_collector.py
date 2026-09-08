import threading

import pytest
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

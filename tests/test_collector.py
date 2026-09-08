import pytest
from server import collector, storage

SCALES = {"cur_power": 1, "cur_voltage": 1, "add_ele": 3, "temp_current": 1}


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

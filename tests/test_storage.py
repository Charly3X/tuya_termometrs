import pytest
from server import storage


@pytest.fixture
def conn(tmp_path):
    c = storage.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_write_returns_row_count(conn):
    assert storage.write(conn, 100, "dev1", {"power": 90.9, "voltage": 236.5}) == 2


def test_series_is_ordered_and_filtered_by_metric(conn):
    storage.write(conn, 200, "dev1", {"power": 2.0})
    storage.write(conn, 100, "dev1", {"power": 1.0})
    storage.write(conn, 150, "dev1", {"voltage": 230.0})
    assert storage.series(conn, "dev1", "power", 0) == [(100, 1.0), (200, 2.0)]


def test_series_respects_the_lower_bound(conn):
    storage.write(conn, 100, "dev1", {"power": 1.0})
    storage.write(conn, 200, "dev1", {"power": 2.0})
    assert storage.series(conn, "dev1", "power", 150) == [(200, 2.0)]


def test_series_of_unknown_device_is_empty(conn):
    assert storage.series(conn, "nobody", "power", 0) == []


def test_power_series_carries_the_last_voltage_forward(conn):
    # MQTT often sends cur_power alone, with voltage arriving in another report
    storage.write(conn, 100, "dev1", {"power": 10.0, "voltage": 230.0})
    storage.write(conn, 110, "dev1", {"power": 11.0})
    storage.write(conn, 120, "dev1", {"voltage": 231.0})
    storage.write(conn, 130, "dev1", {"power": 12.0})
    assert storage.power_series(conn, "dev1", 0) == [
        [100, 10.0, 230.0],
        [110, 11.0, 230.0],
        [130, 12.0, 231.0],
    ]


def test_power_series_before_any_voltage_reports_zero(conn):
    storage.write(conn, 100, "dev1", {"power": 10.0})
    assert storage.power_series(conn, "dev1", 0) == [[100, 10.0, 0.0]]


def test_count_range_groups_by_device(conn):
    storage.write(conn, 100, "dev1", {"power": 1.0, "voltage": 2.0})
    storage.write(conn, 100, "dev2", {"power": 1.0})
    storage.write(conn, 999, "dev1", {"power": 1.0})
    assert storage.count_range(conn, 0, 500) == {"dev1": 2, "dev2": 1}


def test_delete_range_boundaries_are_inclusive(conn):
    for ts in (100, 200, 300):
        storage.write(conn, ts, "dev1", {"power": 1.0})
    assert storage.delete_range(conn, 100, 200) == 2
    assert storage.series(conn, "dev1", "power", 0) == [(300, 1.0)]


def test_delete_range_can_target_one_device(conn):
    storage.write(conn, 100, "dev1", {"power": 1.0})
    storage.write(conn, 100, "dev2", {"power": 1.0})
    storage.delete_range(conn, 0, 500, device="dev1")
    assert storage.series(conn, "dev2", "power", 0) == [(100, 1.0)]


def test_delete_range_can_target_one_metric(conn):
    storage.write(conn, 100, "dev1", {"power": 1.0, "voltage": 2.0})
    storage.delete_range(conn, 0, 500, metric="power")
    assert storage.series(conn, "dev1", "voltage", 0) == [(100, 2.0)]


def test_connect_is_idempotent(tmp_path):
    path = tmp_path / "t.db"
    c1 = storage.connect(path)
    storage.write(c1, 100, "dev1", {"power": 1.0})
    c1.close()
    c2 = storage.connect(path)  # must not wipe or fail on existing schema
    assert storage.series(c2, "dev1", "power", 0) == [(100, 1.0)]
    c2.close()

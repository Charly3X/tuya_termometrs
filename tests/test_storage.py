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


def test_series_bucketed_averages_rows_and_labels_with_bucket_start(conn):
    # Both rows fall inside the same 3600s bucket (ts // 3600 == 0), so the
    # bucket's value is their average, and its label is the bucket's start
    # (0), not either row's own timestamp.
    storage.write(conn, 1000, "dev1", {"temperature": 20.0})
    storage.write(conn, 1010, "dev1", {"temperature": 22.0})
    assert storage.series_bucketed(conn, "dev1", "temperature", 0, 3600) == [[0, 21.0]]


def test_series_bucketed_skips_empty_buckets(conn):
    # A row at ts=1000 (bucket 0) and one at ts=8000 (bucket 2, since
    # 8000 // 3600 == 2) leave bucket 1 (3600..7199) with nothing in it.
    # That bucket must not appear at all -- not as a zero, not as a gap
    # filler.
    storage.write(conn, 1000, "dev1", {"temperature": 20.0})
    storage.write(conn, 8000, "dev1", {"temperature": 30.0})
    result = storage.series_bucketed(conn, "dev1", "temperature", 0, 3600)
    assert result == [[0, 20.0], [7200, 30.0]]
    assert [bucket for bucket, _ in result] == [0, 7200]


def test_series_bucketed_boundary_rows_land_in_different_buckets(conn):
    # ts=3599 is the last second of bucket 0; ts=3600 is the first second
    # of bucket 1. They must not be averaged together.
    storage.write(conn, 3599, "dev1", {"temperature": 10.0})
    storage.write(conn, 3600, "dev1", {"temperature": 20.0})
    result = storage.series_bucketed(conn, "dev1", "temperature", 0, 3600)
    assert result == [[0, 10.0], [3600, 20.0]]


def test_series_bucketed_respects_the_lower_bound(conn):
    storage.write(conn, 1000, "dev1", {"temperature": 20.0})
    storage.write(conn, 8000, "dev1", {"temperature": 30.0})
    assert storage.series_bucketed(conn, "dev1", "temperature", 5000, 3600) == [[7200, 30.0]]


def test_series_bucketed_of_unknown_device_is_empty(conn):
    assert storage.series_bucketed(conn, "nobody", "temperature", 0, 3600) == []


def test_connect_is_idempotent(tmp_path):
    path = tmp_path / "t.db"
    c1 = storage.connect(path)
    storage.write(c1, 100, "dev1", {"power": 1.0})
    c1.close()
    c2 = storage.connect(path)  # must not wipe or fail on existing schema
    assert storage.series(c2, "dev1", "power", 0) == [(100, 1.0)]
    c2.close()

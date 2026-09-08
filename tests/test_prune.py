import pytest
from server import prune, storage


@pytest.fixture
def conn(tmp_path):
    c = storage.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_day_bounds_covers_the_whole_last_day():
    start, end = prune.day_bounds("2026-09-01", "2026-09-03")
    assert end - start == 3 * 86400 - 1  # inclusive of all of Sep 3


def test_prune_older_than_keeps_recent_rows(conn):
    now = 1_000_000_000
    storage.write(conn, now - 400 * 86400, "dev1", {"power": 1.0})
    storage.write(conn, now - 10 * 86400, "dev1", {"power": 2.0})
    assert prune.prune_older_than(conn, 365, now=now) == 1
    assert storage.series(conn, "dev1", "power", 0) == [(now - 10 * 86400, 2.0)]


def test_dry_run_deletes_nothing(conn, capsys):
    storage.write(conn, prune.parse_day("2026-09-02"), "dev1", {"power": 1.0})
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    prune.main(["--from", "2026-09-01", "--to", "2026-09-03", "--database", db])
    assert len(storage.series(conn, "dev1", "power", 0)) == 1
    assert "dev1" in capsys.readouterr().out


def test_yes_actually_deletes(conn):
    storage.write(conn, prune.parse_day("2026-09-02"), "dev1", {"power": 1.0})
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    prune.main(["--from", "2026-09-01", "--to", "2026-09-03", "--database", db, "--yes"])
    assert storage.series(conn, "dev1", "power", 0) == []


def test_device_filter_spares_other_devices(conn):
    ts = prune.parse_day("2026-09-02")
    storage.write(conn, ts, "dev1", {"power": 1.0})
    storage.write(conn, ts, "dev2", {"power": 1.0})
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    prune.main(["--from", "2026-09-01", "--to", "2026-09-03",
                "--database", db, "--device", "dev1", "--yes"])
    assert storage.series(conn, "dev2", "power", 0) == [(ts, 1.0)]

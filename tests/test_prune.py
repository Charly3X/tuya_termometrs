import time

import pytest
from server import prune, storage


@pytest.fixture
def conn(tmp_path):
    c = storage.connect(tmp_path / "t.db")
    yield c
    c.close()


def db_path(conn):
    return conn.execute("PRAGMA database_list").fetchone()[2]


def test_day_bounds_covers_the_whole_last_day():
    start, end = prune.day_bounds("2026-09-01", "2026-09-03")
    assert end - start == 3 * 86400 - 1  # inclusive of all of Sep 3


def test_older_than_bounds_cuts_at_the_right_moment():
    assert prune.older_than_bounds(365, now=1_000_000_000) == (0, 1_000_000_000 - 365 * 86400)


def test_older_than_days_keeps_recent_rows(conn):
    now = int(time.time())
    storage.write(conn, now - 400 * 86400, "dev1", {"power": 1.0})
    storage.write(conn, now - 10 * 86400, "dev1", {"power": 2.0})
    prune.main(["--database", db_path(conn), "--older-than-days", "365", "--yes"])
    assert storage.series(conn, "dev1", "power", 0) == [(now - 10 * 86400, 2.0)]


def test_retention_days_comes_from_settings_when_the_flag_is_absent(conn):
    # retention_days used to be configured but wired to nothing: the real
    # retention was the hardcoded --older-than-days in the README's cron
    # line, so editing settings.json silently did nothing.
    now = int(time.time())
    storage.write(conn, now - 100 * 86400, "dev1", {"power": 1.0})
    storage.write(conn, now - 10 * 86400, "dev1", {"power": 2.0})
    prune.main(
        ["--database", db_path(conn), "--yes"],
        settings_dict={"server": {"retention_days": 30}},
    )
    assert storage.series(conn, "dev1", "power", 0) == [(now - 10 * 86400, 2.0)]


def test_explicit_older_than_days_overrides_settings(conn):
    now = int(time.time())
    storage.write(conn, now - 100 * 86400, "dev1", {"power": 1.0})
    prune.main(
        ["--database", db_path(conn), "--older-than-days", "365", "--yes"],
        settings_dict={"server": {"retention_days": 30}},
    )
    assert len(storage.series(conn, "dev1", "power", 0)) == 1


def test_retention_run_does_not_vacuum(conn, monkeypatch):
    # A VACUUM of the steady-state 2-2.5 GB database rewrites the whole file
    # under an exclusive lock, and the collector's concurrent writes are lost
    # to the busy timeout. The nightly job deletes as much as it inserts, so
    # there is nothing to reclaim anyway.
    calls = []
    monkeypatch.setattr(prune.storage, "vacuum", lambda c: calls.append(c))
    now = int(time.time())
    storage.write(conn, now - 400 * 86400, "dev1", {"power": 1.0})
    prune.main(["--database", db_path(conn), "--older-than-days", "365", "--yes"])
    assert calls == []
    assert storage.series(conn, "dev1", "power", 0) == []


def test_vacuum_flag_forces_a_vacuum_on_the_retention_path(conn, monkeypatch):
    # The obvious response to a full disk is to lower retention_days, but a
    # retention delete alone never shrinks the file -- freed pages go to
    # SQLite's freelist, not back to the filesystem. --vacuum is the opt-in
    # escape hatch for that case.
    calls = []
    monkeypatch.setattr(prune.storage, "vacuum", lambda c: calls.append(c))
    now = int(time.time())
    storage.write(conn, now - 400 * 86400, "dev1", {"power": 1.0})
    prune.main(["--database", db_path(conn), "--older-than-days", "365",
                "--yes", "--vacuum"])
    assert len(calls) == 1


def test_deleting_a_period_by_hand_still_vacuums(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(prune.storage, "vacuum", lambda c: calls.append(c))
    storage.write(conn, prune.parse_day("2026-09-02"), "dev1", {"power": 1.0})
    prune.main(["--from", "2026-09-01", "--to", "2026-09-03",
                "--database", db_path(conn), "--yes"])
    assert len(calls) == 1


def test_dry_run_deletes_nothing(conn, capsys):
    storage.write(conn, prune.parse_day("2026-09-02"), "dev1", {"power": 1.0})
    prune.main(["--from", "2026-09-01", "--to", "2026-09-03", "--database", db_path(conn)])
    assert len(storage.series(conn, "dev1", "power", 0)) == 1
    assert "dev1" in capsys.readouterr().out


def test_yes_actually_deletes(conn):
    storage.write(conn, prune.parse_day("2026-09-02"), "dev1", {"power": 1.0})
    prune.main(["--from", "2026-09-01", "--to", "2026-09-03",
                "--database", db_path(conn), "--yes"])
    assert storage.series(conn, "dev1", "power", 0) == []


def test_device_filter_spares_other_devices(conn):
    ts = prune.parse_day("2026-09-02")
    storage.write(conn, ts, "dev1", {"power": 1.0})
    storage.write(conn, ts, "dev2", {"power": 1.0})
    prune.main(["--from", "2026-09-01", "--to", "2026-09-03",
                "--database", db_path(conn), "--device", "dev1", "--yes"])
    assert storage.series(conn, "dev2", "power", 0) == [(ts, 1.0)]


def test_half_a_range_is_rejected(conn):
    with pytest.raises(SystemExit):
        prune.main(["--database", db_path(conn), "--from", "2026-09-01"])


def test_a_range_and_a_retention_age_together_are_rejected(conn):
    with pytest.raises(SystemExit):
        prune.main(["--database", db_path(conn), "--from", "2026-09-01",
                    "--to", "2026-09-03", "--older-than-days", "365"])

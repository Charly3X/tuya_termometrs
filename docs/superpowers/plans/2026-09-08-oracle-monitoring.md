# Oracle Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect Tuya statistics around the clock on an Oracle server and let the KDE widget draw its chart from that server instead of from whatever the desktop happened to be awake for.

**Architecture:** A collector service subscribes to Tuya's MQTT push for devices that can be decoded from it and polls the shadow endpoint for those that cannot, writing everything into SQLite in human units. A separate read-only HTTP service serves history. The widget asks that service first and silently falls back to its existing local file.

**Tech Stack:** Python 3.11, `tuya-device-sharing-sdk`, `paho-mqtt` (pulled in by the SDK), stdlib `sqlite3` and `http.server`, systemd. pytest for tests. No new runtime dependencies.

Spec: [2026-09-08-oracle-monitoring-design.md](../specs/2026-09-08-oracle-monitoring-design.md)

## Synchronisation: there isn't any, deliberately

The two stores are never reconciled, and that is a decision rather than an
oversight. Worth stating plainly because "the widget reads from the server"
sounds like sync and is not.

- **The server is the single source of truth.** The local `power_history.json`
  is an emergency cache, shrunk to six hours in Task 8.
- **Nothing travels upward.** The widget never pushes readings to the server,
  so the API keeps having no write path at all. That matters because the
  server also holds `sharing_token.json`, a key to the whole Smart Life
  account.
- **Resolution does not suffer while the desktop is on.** The sockets report to
  the cloud in response to local polling, so whatever the widget sees locally
  the collector sees through MQTT at the same cadence.
- **The one accepted hole:** if the *server* is down while the desktop is up,
  those readings exist only in the local cache and never reach the database.
  The chart falls back and stays usable during the outage, but the history
  keeps a gap afterwards. Closing it would need an ingest endpoint, which is
  not worth putting on the machine that holds the account credentials.
- **Both sides timestamp in Unix seconds, UTC.** No local time anywhere, in the
  database or on the wire. Do not "fix" this into local time.

## Global Constraints

- Python interpreter is always `./venv/bin/python3`. Never `python3`.
- No new runtime dependencies. `http.server` and `sqlite3` instead of a web framework. pytest is dev-only, in `requirements-dev.txt`.
- Git commit messages must be written in English (AGENTS.md).
- `tuya_sharing_api.prefer_ipv4()` must run before the first cloud request in every entry point. Without it each request costs ~180s instead of 0.2s.
- `Manager.refresh_mq()` must never be used. It subscribes to device topics only when `set_up` is true, and no device on this account has it — measured 0 events in 90s. Build `SharingMQ(customer_api, home_ids, devices)` directly instead; measured 27 events in 90s.
- Values are stored in human units (W, V, mA, kWh, °C, %), converted once on write using the `scale` Tuya declares per status code — **except** where `units.SCALE_OVERRIDES` says otherwise. `add_ele` is declared `scale: 3, step: 100` but the plugs report 0.01 kWh in values like 9 and 45, so the declared scale is wrong for this hardware and must not be trusted. Verified against the local DPS value and against the fridge's actual consumption on 2026-09-08.
- Secrets live in `config.json`, behaviour lives in `settings.json`. `config.json` and `settings.json` are gitignored; only `.example` files are committed.
- Retention is 365 days.
- The chart contract with QML is `[[ts, power, voltage], ...]`. QML reads only indices 0 and 1, but the third element is kept so the server and the local fallback return the same shape.

## File Structure

Shared, deployed to both machines by `git pull`:

| File | Responsibility |
|---|---|
| `settings.py` | Load `settings.json`, deep-merge over defaults |
| `settings.json.example` | Committed template for both machines |
| `history_client.py` | Desktop: fetch history over HTTP, fall back to local file |
| `requirements-dev.txt` | pytest |

Server package under `server/`:

| File | Responsibility |
|---|---|
| `server/units.py` | Map Tuya status codes to metric names and human units |
| `server/storage.py` | SQLite: schema, writes, queries, range deletes |
| `server/roles.py` | Decide push vs poll per device, discover unit scales |
| `server/collector.py` | The service: MQTT push + shadow polling → storage |
| `server/api.py` | Read-only HTTP history service |
| `server/prune.py` | CLI for deleting a period; also the nightly retention job |
| `server/import_legacy.py` | One-off import of `power_history.json` |
| `server/systemd/*.service` | Unit files |

Tests under `tests/`, one module per unit above.

---

### Task 1: Test harness and settings loader

**Files:**
- Create: `requirements-dev.txt`, `settings.py`, `settings.json.example`, `tests/test_settings.py`
- Modify: `tuya_client.py`, `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: `settings.load_settings(path=None) -> dict` and `settings.DEFAULTS`. Every later task reads configuration through this.

- [ ] **Step 1: Install pytest**

```bash
printf 'pytest\n' > requirements-dev.txt
./venv/bin/pip install -q -r requirements-dev.txt
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_settings.py`:

```python
import json
import settings


def test_missing_file_returns_defaults(tmp_path):
    result = settings.load_settings(tmp_path / "nope.json")
    assert result == settings.DEFAULTS
    assert result is not settings.DEFAULTS  # must be a copy, not the shared dict


def test_partial_override_keeps_other_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"history_timeout": 9}))
    result = settings.load_settings(path)
    assert result["history_timeout"] == 9
    assert result["cloud_backend"] == "sharing"


def test_nested_override_does_not_wipe_siblings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"server": {"port": 9999}}))
    result = settings.load_settings(path)
    assert result["server"]["port"] == 9999
    assert result["server"]["retention_days"] == 365


def test_defaults_are_not_mutated_by_a_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"server": {"port": 1}}))
    settings.load_settings(path)
    assert settings.DEFAULTS["server"]["port"] == 8080
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./venv/bin/python3 -m pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'settings'`

- [ ] **Step 4: Write the implementation**

Create `settings.py`:

```python
#!/usr/bin/env python3
"""
Behaviour settings, kept apart from secrets.

`config.json` holds credentials, device ids and tokens and is never committed.
This file holds everything else: retention, intervals, ports, addresses. Both
machines use the same filename with different contents.
"""
import copy
import json
from pathlib import Path

SETTINGS_FILE = Path(__file__).parent / "settings.json"

DEFAULTS = {
    "region": "eu",
    "cloud_backend": "sharing",
    "history_server": "",
    "history_timeout": 3,
    "server": {
        "database": "readings.db",
        "port": 8080,
        "retention_days": 365,
        "poll_interval": 300,
    },
}


def _merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(path=None):
    """Settings from disk merged over DEFAULTS. Missing file means all defaults."""
    path = Path(path) if path else SETTINGS_FILE
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    with open(path) as f:
        return _merge(DEFAULTS, json.load(f))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/python3 -m pytest tests/test_settings.py -v`
Expected: 4 passed

- [ ] **Step 6: Create the committed template**

Create `settings.json.example`:

```json
{
    "region": "eu",
    "cloud_backend": "sharing",

    "history_server": "http://SERVER_IP:8080",
    "history_timeout": 3,

    "server": {
        "database": "/var/lib/tuya/readings.db",
        "port": 8080,
        "retention_days": 365,
        "poll_interval": 300
    }
}
```

- [ ] **Step 7: Move `cloud_backend` out of config.json**

In `tuya_client.py`, replace the body of `use_sharing_backend`:

```python
def use_sharing_backend(config):
    """
    Whether to read the cloud through the Smart Life sharing SDK.

    Preferred over tinytuya.Cloud because it does not need an IoT Core
    subscription. Set "cloud_backend": "iot_core" in settings.json to force
    the old path.
    """
    if not config:
        return False
    try:
        backend = load_settings().get("cloud_backend", "sharing")
        return backend == "sharing" and tuya_sharing_api.load_session() is not None
    except Exception:
        return False
```

Add the import at the top of `tuya_client.py`:

```python
from settings import load_settings
```

In `tuya_sharing_api.py`, **delete** `is_available()` entirely. `use_sharing_backend` was its only caller and now asks `load_session()` directly, so leaving it would be dead code:

```python
def is_available(config):
    """True when the sharing backend is configured and authenticated."""
    if config.get("cloud_backend", "sharing") != "sharing":
        return False
    return load_session() is not None
```

Confirm nothing else calls it before deleting:

```bash
grep -rn "is_available" --include="*.py" --include="*.qml" . | grep -v venv
```

Expected: no matches outside the definition itself.

- [ ] **Step 8: Verify nothing regressed**

Run: `./venv/bin/python3 tuya_client.py thermometers cloud`
Expected: three real temperatures, not `--`

Run: `./venv/bin/python3 tuya_client.py socket local`
Expected: two sockets with power values

- [ ] **Step 9: Ignore the real settings file**

```bash
printf 'settings.json\n' >> .gitignore
```

- [ ] **Step 10: Commit**

```bash
git add requirements-dev.txt settings.py settings.json.example tests/test_settings.py tuya_client.py tuya_sharing_api.py .gitignore
git commit -m "Add settings file separate from secrets"
```

---

### Task 2: Unit conversion

**Files:**
- Create: `server/__init__.py`, `server/units.py`, `tests/test_units.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `units.convert(code: str, value, scales: dict[str, int]) -> tuple[str, float] | None` and `units.FALLBACK_SCALES: dict[str, int]`. The collector uses both.

- [ ] **Step 1: Write the failing test**

Create `tests/test_units.py`:

```python
from server import units

SOCKET_SCALES = {"cur_power": 1, "cur_voltage": 1, "cur_current": 0, "add_ele": 3}


def test_power_uses_declared_scale():
    assert units.convert("cur_power", 909, SOCKET_SCALES) == ("power", 90.9)


def test_energy_ignores_the_scale_tuya_declares():
    # Tuya declares scale 3 for add_ele, but this hardware reports 0.01 kWh
    # units. Measured 2026-09-08: raw 45 while the fridge had used ~0.45 kWh.
    # The override must win even when the specification is passed in.
    assert units.convert("add_ele", 45, {"add_ele": 3}) == ("energy", 0.45)


def test_scale_zero_passes_value_through():
    assert units.convert("cur_current", 435, SOCKET_SCALES) == ("current", 435.0)


def test_temperature_from_either_code_name():
    assert units.convert("va_temperature", 236, {"va_temperature": 1}) == ("temperature", 23.6)
    assert units.convert("temp_current", 244, {"temp_current": 1}) == ("temperature", 24.4)


def test_battery_enum_becomes_a_percentage():
    assert units.convert("battery_state", "middle", {}) == ("battery", 40.0)


def test_unknown_scale_falls_back_to_the_table():
    # A device with a broken product definition returns no specifications.
    metric, value = units.convert("cur_power", 909, {})
    assert (metric, value) == ("power", 90.9)


def test_untracked_code_is_ignored():
    assert units.convert("relay_status", "2", {}) is None


def test_boolean_switch_is_ignored_not_treated_as_one():
    assert units.convert("switch_1", True, {}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python3 -m pytest tests/test_units.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 3: Write the implementation**

Create empty `server/__init__.py`, then `server/units.py`:

```python
#!/usr/bin/env python3
"""
Tuya status codes to metric names and human units.

Tuya sends integers in its own scales: cur_power 909 means 90.9 W. The scale
is declared per code in the product specification, so we ask for it rather
than hardcoding. FALLBACK_SCALES covers devices whose specification comes back
empty because their product definition is broken in Tuya's catalog.
"""

METRIC_NAMES = {
    "cur_power": "power",
    "cur_voltage": "voltage",
    "cur_current": "current",
    "add_ele": "energy",
    "va_temperature": "temperature",
    "temp_current": "temperature",
    "va_humidity": "humidity",
    "humidity_value": "humidity",
    "battery_state": "battery",
    "battery_percentage": "battery",
}

FALLBACK_SCALES = {
    "cur_power": 1,
    "cur_voltage": 1,
    "cur_current": 0,
    "add_ele": 2,
    "va_temperature": 1,
    "temp_current": 1,
    "va_humidity": 0,
    "humidity_value": 0,
    "battery_percentage": 0,
}

# Codes where the hardware contradicts its own declared specification.
# These win over whatever Tuya reports, so do not "simplify" them away.
SCALE_OVERRIDES = {
    # Tuya declares add_ele as {"scale": 3, "step": 100}, which would mean
    # 0.001 kWh units arriving in multiples of 100. The plugs report 9 and 45.
    # Measured 2026-09-08: the local DPS 20 value and the cloud add_ele value
    # are the same number, and 45 -> 0.45 kWh matches a fridge drawing ~69 W
    # for ten hours at roughly half duty. 0.045 kWh would be 40 minutes of
    # running in ten hours, which is impossible for a fridge that is on.
    "add_ele": 2,
}

BATTERY_STATE = {"low": 10.0, "middle": 40.0, "high": 80.0}


def convert(code, value, scales):
    """
    (metric_name, value_in_human_units), or None if the code is not tracked
    or the value is not numeric.
    """
    metric = METRIC_NAMES.get(code)
    if metric is None:
        return None

    if isinstance(value, str):
        if code == "battery_state":
            return metric, BATTERY_STATE.get(value.lower(), 50.0)
        return None

    # bool is a subclass of int, and switch_1 must not become a number
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    if code in SCALE_OVERRIDES:
        scale = SCALE_OVERRIDES[code]
    else:
        scale = scales.get(code, FALLBACK_SCALES.get(code, 0))
    return metric, value / (10 ** scale)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python3 -m pytest tests/test_units.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add server/__init__.py server/units.py tests/test_units.py
git commit -m "Add unit conversion using Tuya declared scales"
```

---

### Task 3: SQLite storage

**Files:**
- Create: `server/storage.py`, `tests/test_storage.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `storage.connect(path) -> sqlite3.Connection`
  - `storage.write(conn, ts: int, device: str, values: dict[str, float]) -> int`
  - `storage.series(conn, device, metric, since_ts, until_ts=None) -> list[tuple[int, float]]`
  - `storage.power_series(conn, device, since_ts) -> list[list]` shaped `[ts, power, voltage]`
  - `storage.count_range(conn, start_ts, end_ts, device=None, metric=None) -> dict[str, int]`
  - `storage.delete_range(conn, start_ts, end_ts, device=None, metric=None) -> int`
  - `storage.vacuum(conn) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python3 -m pytest tests/test_storage.py -v`
Expected: FAIL with `ImportError: cannot import name 'storage'`

- [ ] **Step 3: Write the implementation**

Create `server/storage.py`:

```python
#!/usr/bin/env python3
"""
SQLite storage for readings.

Narrow format (ts, device, metric, value): new metrics need no migration, and
partial MQTT reports — sometimes cur_power alone, sometimes with cur_voltage —
fit naturally because we write only what arrived.
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    ts     INTEGER NOT NULL,
    device TEXT    NOT NULL,
    metric TEXT    NOT NULL,
    value  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings ON readings(device, metric, ts);
"""


def connect(path):
    """Open the database, creating the schema if needed."""
    conn = sqlite3.connect(str(path), timeout=30)
    # WAL lets the API read while the collector writes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def write(conn, ts, device, values):
    """Insert one row per metric. Returns the number of rows written."""
    rows = [(ts, device, metric, float(value)) for metric, value in values.items()]
    if rows:
        conn.executemany(
            "INSERT INTO readings (ts, device, metric, value) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    return len(rows)


def series(conn, device, metric, since_ts, until_ts=None):
    """[(ts, value), ...] ordered by time."""
    sql = "SELECT ts, value FROM readings WHERE device = ? AND metric = ? AND ts >= ?"
    args = [device, metric, since_ts]
    if until_ts is not None:
        sql += " AND ts <= ?"
        args.append(until_ts)
    sql += " ORDER BY ts"
    return [(row[0], row[1]) for row in conn.execute(sql, args)]


def power_series(conn, device, since_ts):
    """
    [[ts, power, voltage], ...] — the shape the widget's chart expects.

    Voltage is reported less often than power, so the last known value is
    carried forward instead of dropping power points that have no voltage
    at the same timestamp.
    """
    # The CASE is not decoration. write() stores one row per metric, so a
    # report carrying both power and voltage produces two rows with the SAME
    # ts. Under a plain "ORDER BY ts" SQLite probes the index once per value of
    # the IN list -- 'power' first -- and the stable sort then leaves the power
    # row ahead of the voltage row it should have been paired with, so that
    # reading carries a stale voltage. Verified: the plain version fails
    # test_power_series_carries_the_last_voltage_forward, returning
    # [100, 10.0, 0.0] instead of [100, 10.0, 230.0]. The explicit tie-break
    # fixes it deterministically rather than relying on rowid order.
    rows = conn.execute(
        "SELECT ts, metric, value FROM readings "
        "WHERE device = ? AND metric IN ('power', 'voltage') AND ts >= ? "
        "ORDER BY ts, CASE WHEN metric = 'voltage' THEN 0 ELSE 1 END",
        (device, since_ts),
    )

    result = []
    voltage = 0.0
    for ts, metric, value in rows:
        if metric == "voltage":
            voltage = value
        else:
            result.append([ts, value, voltage])
    return result


def _range_clause(start_ts, end_ts, device, metric):
    sql = " WHERE ts >= ? AND ts <= ?"
    args = [start_ts, end_ts]
    if device:
        sql += " AND device = ?"
        args.append(device)
    if metric:
        sql += " AND metric = ?"
        args.append(metric)
    return sql, args


def count_range(conn, start_ts, end_ts, device=None, metric=None):
    """{device_id: row_count} for the range. Used by prune to show the damage first."""
    clause, args = _range_clause(start_ts, end_ts, device, metric)
    sql = "SELECT device, COUNT(*) FROM readings" + clause + " GROUP BY device"
    return {row[0]: row[1] for row in conn.execute(sql, args)}


def delete_range(conn, start_ts, end_ts, device=None, metric=None):
    """Delete rows in the range, boundaries inclusive. Returns rows deleted."""
    clause, args = _range_clause(start_ts, end_ts, device, metric)
    cursor = conn.execute("DELETE FROM readings" + clause, args)
    conn.commit()
    return cursor.rowcount


def vacuum(conn):
    """Return freed pages to the filesystem. Without this a delete frees nothing."""
    conn.execute("VACUUM")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python3 -m pytest tests/test_storage.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add server/storage.py tests/test_storage.py
git commit -m "Add SQLite storage for readings"
```

---

### Task 4: Prune tool and retention

**Files:**
- Create: `server/prune.py`, `tests/test_prune.py`

**Interfaces:**
- Consumes: `storage.connect`, `storage.count_range`, `storage.delete_range`, `storage.vacuum`.
- Produces: `prune.parse_day(text) -> int`, `prune.day_bounds(from_text, to_text) -> tuple[int, int]`, `prune.prune_older_than(conn, days, now=None) -> int`, `prune.main(argv) -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_prune.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python3 -m pytest tests/test_prune.py -v`
Expected: FAIL with `ImportError: cannot import name 'prune'`

- [ ] **Step 3: Write the implementation**

Create `server/prune.py`:

```python
#!/usr/bin/env python3
"""
Delete history: either a period chosen by hand, or everything past the
retention age. One implementation serves both so they cannot drift apart.

    prune.py --from 2026-09-01 --to 2026-09-03 [--device ID] [--metric power]
    prune.py --older-than-days 365 --yes

Without --yes nothing is deleted; the tool only reports what would go.
"""
import argparse
import sys
import time
from datetime import datetime, timezone

from server import storage


def parse_day(text):
    """Unix timestamp of midnight UTC on the given YYYY-MM-DD."""
    day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(day.timestamp())


def day_bounds(from_text, to_text):
    """(start, end) covering both days completely, end inclusive."""
    return parse_day(from_text), parse_day(to_text) + 86400 - 1


def prune_older_than(conn, days, now=None):
    """Delete everything older than `days`. Returns rows deleted."""
    now = int(now if now is not None else time.time())
    cutoff = now - days * 86400
    return storage.delete_range(conn, 0, cutoff)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Delete Tuya history")
    parser.add_argument("--database", required=True)
    parser.add_argument("--from", dest="from_day", help="YYYY-MM-DD, inclusive")
    parser.add_argument("--to", dest="to_day", help="YYYY-MM-DD, inclusive")
    parser.add_argument("--older-than-days", type=int)
    parser.add_argument("--device")
    parser.add_argument("--metric")
    parser.add_argument("--yes", action="store_true", help="actually delete")
    args = parser.parse_args(argv)

    if args.older_than_days is None and not (args.from_day and args.to_day):
        parser.error("give either --older-than-days or both --from and --to")

    conn = storage.connect(args.database)
    try:
        if args.older_than_days is not None:
            start, end = 0, int(time.time()) - args.older_than_days * 86400
        else:
            start, end = day_bounds(args.from_day, args.to_day)

        counts = storage.count_range(conn, start, end, args.device, args.metric)
        total = sum(counts.values())

        if not total:
            print("Nothing matches that range.")
            return 0

        for device, count in sorted(counts.items()):
            print(f"  {device}  {count} rows")
        print(f"  total: {total}")

        if not args.yes:
            print("\nDry run. Re-run with --yes to delete.")
            return 0

        deleted = storage.delete_range(conn, start, end, args.device, args.metric)
        storage.vacuum(conn)
        print(f"\nDeleted {deleted} rows and vacuumed.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python3 -m pytest tests/test_prune.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add server/prune.py tests/test_prune.py
git commit -m "Add prune tool for manual and age-based deletion"
```

---

### Task 5: Device roles and scales

**Files:**
- Create: `server/roles.py`, `tests/test_roles.py`

**Interfaces:**
- Consumes: a `CustomerApi`-shaped object exposing `.get(path, params=None) -> dict`.
- Produces: `roles.device_profile(api, device_id) -> {"push": bool, "scales": dict[str, int]}` and `roles.classify(api, device_ids) -> (push_ids, poll_ids, scales_by_device)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_roles.py`:

```python
from server import roles


class FakeApi:
    """Returns canned responses keyed by the path prefix."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(path)
        for prefix, response in self.responses.items():
            if prefix in path:
                return response
        raise AssertionError(f"unexpected path {path}")


WORKING_SOCKET = {
    "/status": {"success": True, "result": {
        "dpStatusRelationDTOS": [{"dpId": 19, "statusCode": "cur_power"}]}},
    "/specifications": {"success": True, "result": {"status": [
        {"code": "cur_power", "values": '{"unit":"W","scale":1}'},
        {"code": "add_ele", "values": '{"unit":"kW·h","scale":3}'},
    ]}},
}

BROKEN_SENSOR = {
    "/status": {"success": True, "result": {"dpStatusRelationDTOS": []}},
    "/specifications": {"success": True, "result": {"status": []}},
}


def test_device_with_dp_map_uses_push():
    profile = roles.device_profile(FakeApi(WORKING_SOCKET), "dev1")
    assert profile["push"] is True


def test_scales_come_from_the_specification():
    profile = roles.device_profile(FakeApi(WORKING_SOCKET), "dev1")
    assert profile["scales"]["cur_power"] == 1
    # roles reports what Tuya declares, unedited. add_ele is declared as 3 and
    # that is wrong for this hardware, but correcting it is units.SCALE_OVERRIDES'
    # job, not this function's -- keep the two concerns apart.
    assert profile["scales"]["add_ele"] == 3


def test_device_with_empty_dp_map_uses_polling():
    profile = roles.device_profile(FakeApi(BROKEN_SENSOR), "dev2")
    assert profile["push"] is False


def test_broken_device_still_gets_fallback_scales():
    profile = roles.device_profile(FakeApi(BROKEN_SENSOR), "dev2")
    assert profile["scales"]["temp_current"] == 1


def test_api_failure_falls_back_to_polling_not_a_crash():
    class Broken:
        def get(self, path, params=None):
            raise RuntimeError("network down")

    profile = roles.device_profile(Broken(), "dev3")
    assert profile["push"] is False
    assert profile["scales"]["cur_power"] == 1


def test_classify_splits_the_two_groups():
    class Mixed:
        def get(self, path, params=None):
            source = WORKING_SOCKET if "dev1" in path else BROKEN_SENSOR
            for prefix, response in source.items():
                if prefix in path:
                    return response
            raise AssertionError(path)

    push, poll, scales = roles.classify(Mixed(), ["dev1", "dev2"])
    assert push == ["dev1"]
    assert poll == ["dev2"]
    assert set(scales) == {"dev1", "dev2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python3 -m pytest tests/test_roles.py -v`
Expected: FAIL with `ImportError: cannot import name 'roles'`

- [ ] **Step 3: Write the implementation**

Create `server/roles.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python3 -m pytest tests/test_roles.py -v`
Expected: 6 passed

- [ ] **Step 5: Verify against the real account**

Run:

```bash
./venv/bin/python3 -c "
import tuya_sharing_api as ts
from server import roles
api = ts.get_api()
push, poll, scales = roles.classify(api, [
    'bf973442af478b5404fupa','bf891cacc88ce07d8esvr2',
    'bfb3a145aa1b20e1e0lbdq','bfac5ed45637af7f13fzwd','bf19c49981834da7bdkoqe'])
print('push:', push)
print('poll:', poll)
print('socket scales:', scales['bf973442af478b5404fupa'])
"
```

Expected: the two sockets and `bfb3a145` in `push`; `bfac5ed4` and `bf19c499` in `poll`; socket scales showing `cur_power: 1` and `add_ele: 3`.

- [ ] **Step 6: Commit**

```bash
git add server/roles.py tests/test_roles.py
git commit -m "Detect device read mode and unit scales from Tuya"
```

---

### Task 6: Collector

**Files:**
- Create: `server/collector.py`, `tests/test_collector.py`

**Interfaces:**
- Consumes: `storage`, `units`, `roles`, `tuya_sharing_api`, `settings`.
- Produces: `collector.record(conn, device_id, status, scales, ts) -> int` (the whole testable core) and `collector.run(settings_dict, device_ids) -> None` (the service loop).

- [ ] **Step 1: Write the failing test**

Create `tests/test_collector.py`:

```python
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


def test_push_listener_writes_from_the_mqtt_thread(tmp_path):
    """
    The regression guard for the defect that cost this task a fix round.

    paho-mqtt calls update_device on its own thread. A sqlite3 connection
    created on the main thread cannot be used there, so a listener sharing the
    collector's connection wrote nothing while looking healthy. Driving the
    write from a real second thread is the only way to catch that -- calling
    the listener on the main thread passes against the broken design too.
    """
    import threading

    database = tmp_path / "t.db"
    listener = collector._PushListener(str(database), {"dev1": SCALES})

    class FakeDevice:
        id = "dev1"
        name = "socket"
        status = {"cur_power": 909}

    thread = threading.Thread(
        target=lambda: listener.update_device(FakeDevice(), ["cur_power"])
    )
    thread.start()
    thread.join()

    # update_device logs and swallows its exceptions, so a raised error would
    # never reach this thread. The written row is the only honest evidence.
    conn = storage.connect(database)
    rows = storage.series(conn, "dev1", "power", 0)
    conn.close()

    assert len(rows) == 1
    assert rows[0][1] == 90.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python3 -m pytest tests/test_collector.py -v`
Expected: FAIL with `ImportError: cannot import name 'collector'`

- [ ] **Step 3: Write the implementation**

Create `server/collector.py`:

```python
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
    """
    Holds its own database connection, opened lazily on whichever thread
    first uses it.

    paho-mqtt dispatches callbacks on its own thread, and a sqlite3 connection
    may only be used on the thread that created it. Sharing the collector's
    main connection here raises ProgrammingError on every push write, which
    loses all socket power data while shadow-polled sensors keep recording --
    so the collector looks half-alive instead of broken. Measured before this
    was fixed: 27 push callbacks arrived, 0 rows were written.

    Two connections against a WAL database is the case WAL exists for.
    Do not "simplify" this to check_same_thread=False on one shared
    connection: that removes the guard without removing the hazard, since two
    threads would then interleave commits.
    """

    def __init__(self, database, scales):
        self.database = database
        self.scales = scales
        self._local = threading.local()

    def _conn(self):
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

    conn = storage.connect(settings_dict["server"]["database"])
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
    start_push(manager, settings_dict["server"]["database"], scales)

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python3 -m pytest tests/test_collector.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the collector against the real account for two minutes**

```bash
timeout 120 ./venv/bin/python3 -m server.collector 2>&1 | tail -20
```

Expected: log lines listing push and poll groups, then no errors. Afterwards:

```bash
./venv/bin/python3 -c "
from server import storage
c = storage.connect('readings.db')
for row in c.execute('SELECT device, metric, COUNT(*) FROM readings GROUP BY device, metric'):
    print(row)
"
```

Expected: `power` rows for both sockets, `temperature` rows for all three sensors.

- [ ] **Step 6: Commit**

```bash
git add server/collector.py tests/test_collector.py
git commit -m "Add collector service using MQTT push and shadow polling"
```

---

### Task 7: Read-only HTTP API

**Files:**
- Create: `server/api.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `storage.power_series`, `storage.series`.
- Produces: `api.make_handler(conn, token) -> class` and `api.serve(conn, token, port) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api.py`:

```python
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from server import api, storage

TOKEN = "secret-token"


@pytest.fixture
def base_url(tmp_path):
    conn = storage.connect(tmp_path / "t.db")
    storage.write(conn, 1000, "dev1", {"power": 90.9, "voltage": 236.5})
    storage.write(conn, 1010, "dev1", {"power": 80.0})
    storage.write(conn, 1020, "dev2", {"temperature": 23.6})

    server = ThreadingHTTPServer(("127.0.0.1", 0), api.make_handler(conn, TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    conn.close()


def fetch(url, token=TOKEN):
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_history_returns_the_widget_shape(base_url):
    result = fetch(f"{base_url}/history?device=dev1&hours=99999")
    assert result == [[1000, 90.9, 236.5], [1010, 80.0, 236.5]]


def test_series_returns_pairs(base_url):
    result = fetch(f"{base_url}/series?device=dev2&metric=temperature&hours=99999")
    assert result == [[1020, 23.6]]


def test_unknown_device_is_an_empty_list_not_an_error(base_url):
    assert fetch(f"{base_url}/history?device=nobody&hours=24") == []


def test_missing_token_is_rejected(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/history?device=dev1&hours=24", token=None)
    assert excinfo.value.code == 401


def test_wrong_token_is_rejected(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/history?device=dev1&hours=24", token="guess")
    assert excinfo.value.code == 401


def test_unknown_path_is_404(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/wat")
    assert excinfo.value.code == 404


def test_there_is_no_write_endpoint(base_url):
    request = urllib.request.Request(
        f"{base_url}/history?device=dev1&hours=24", data=b"{}", method="POST"
    )
    request.add_header("Authorization", f"Bearer {TOKEN}")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=5)
    assert excinfo.value.code == 501
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python3 -m pytest tests/test_api.py -v`
Expected: FAIL with `ImportError: cannot import name 'api'`

- [ ] **Step 3: Write the implementation**

Create `server/api.py`:

```python
#!/usr/bin/env python3
"""
Read-only history service.

Deliberately has no write path of any kind. The machine running this also
holds sharing_token.json, which is a key to the whole Smart Life account
including device control, so this service must never be able to act on it.
"""
import hmac
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from server import storage

log = logging.getLogger("api")


def make_handler(conn, token):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self):
            header = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not header.startswith(prefix):
                return False
            return hmac.compare_digest(header[len(prefix):], token)

        def do_GET(self):
            if not self._authorised():
                self._send(401, {"error": "unauthorised"})
                return

            url = urlparse(self.path)
            query = parse_qs(url.query)
            device = (query.get("device") or [""])[0]
            hours = int((query.get("hours") or ["24"])[0])
            since = int(time.time()) - hours * 3600

            if url.path == "/history":
                self._send(200, storage.power_series(conn, device, since))
            elif url.path == "/series":
                metric = (query.get("metric") or ["power"])[0]
                rows = storage.series(conn, device, metric, since)
                self._send(200, [[ts, value] for ts, value in rows])
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            log.info(fmt, *args)

    return Handler


def serve(conn, token, port):
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(conn, token))
    log.info("listening on port %s", port)
    server.serve_forever()


if __name__ == "__main__":
    import json as _json
    from pathlib import Path

    from settings import load_settings

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    app_settings = load_settings()
    config = _json.loads((Path(__file__).parent.parent / "config.json").read_text())
    serve(
        storage.connect(app_settings["server"]["database"]),
        config["history_token"],
        app_settings["server"]["port"],
    )
```

Note: `BaseHTTPRequestHandler` answers unimplemented methods such as POST with 501 by itself, which is why the test expects 501 and no `do_POST` exists.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python3 -m pytest tests/test_api.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add server/api.py tests/test_api.py
git commit -m "Add read-only history API"
```

---

### Task 8: Widget history client

**Files:**
- Create: `history_client.py`, `tests/test_history_client.py`
- Modify: `tuya_client.py` (the `history` branch of `__main__`), `tuya_history.py`, `contents/ui/main.qml`

**Interfaces:**
- Consumes: `settings.load_settings`, `tuya_history.get_device_history`.
- Produces: `history_client.get_history(settings_dict, token, device_id, hours) -> tuple[list, str]` where the second element is `"server"` or `"local"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_history_client.py`:

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import history_client

REMOTE_ROWS = [[100, 90.9, 236.5]]


@pytest.fixture
def server_url():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            body = json.dumps(REMOTE_ROWS).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_uses_the_server_when_it_answers(server_url, monkeypatch):
    monkeypatch.setattr(history_client, "local_history", lambda d, h: [["local"]])
    rows, source = history_client.get_history(
        {"history_server": server_url, "history_timeout": 3}, "tok", "dev1", 1
    )
    assert rows == REMOTE_ROWS
    assert source == "server"


def test_falls_back_when_the_server_is_unreachable(monkeypatch):
    monkeypatch.setattr(history_client, "local_history", lambda d, h: [["local"]])
    rows, source = history_client.get_history(
        {"history_server": "http://127.0.0.1:1", "history_timeout": 1},
        "tok", "dev1", 1,
    )
    assert rows == [["local"]]
    assert source == "local"


def test_falls_back_when_no_server_is_configured(monkeypatch):
    monkeypatch.setattr(history_client, "local_history", lambda d, h: [["local"]])
    rows, source = history_client.get_history(
        {"history_server": "", "history_timeout": 3}, "tok", "dev1", 1
    )
    assert rows == [["local"]]
    assert source == "local"


def test_empty_server_answer_falls_back_instead_of_blanking_the_chart(monkeypatch):
    """
    An empty list is a valid HTTP answer, not a failure -- but treating it as
    data blanks the chart right after deployment, while the collector has no
    rows for this device yet and the local file still does.
    """
    monkeypatch.setattr(history_client, "local_history", lambda d, h: [["local"]])
    monkeypatch.setattr(history_client, "fetch_remote", lambda *a, **k: [])
    rows, source = history_client.get_history(
        {"history_server": "http://example", "history_timeout": 3}, "tok", "dev1", 1
    )
    assert rows == [["local"]]
    assert source == "local"


def test_empty_from_both_sides_is_reported_as_server(monkeypatch):
    """Nothing anywhere is not a fallback situation; do not cry wolf."""
    monkeypatch.setattr(history_client, "local_history", lambda d, h: [])
    monkeypatch.setattr(history_client, "fetch_remote", lambda *a, **k: [])
    rows, source = history_client.get_history(
        {"history_server": "http://example", "history_timeout": 3}, "tok", "dev1", 1
    )
    assert rows == []
    assert source == "server"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python3 -m pytest tests/test_history_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'history_client'`

- [ ] **Step 3: Write the implementation**

Create `history_client.py`:

```python
#!/usr/bin/env python3
"""
Chart history for the widget: from the collector server if it answers,
from the local file if it does not.

The fallback is what makes the server optional. With no server configured, or
with the network down, the widget behaves exactly as it did before.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from tuya_history import get_device_history


def local_history(device_id, hours):
    """Separate function so tests can replace it."""
    return get_device_history(device_id, hours)


def fetch_remote(base_url, token, device_id, hours, timeout):
    """[[ts, power, voltage], ...] from the server, or None if it did not answer."""
    query = urllib.parse.urlencode({"device": device_id, "hours": hours})
    request = urllib.request.Request(f"{base_url.rstrip('/')}/history?{query}")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception:
        return None


def get_history(settings_dict, token, device_id, hours):
    """
    (rows, source) where source is "server" or "local".

    The source is returned so the widget can say which one it drew, instead of
    silently showing a gappy local chart that looks like real data.
    """
    base_url = settings_dict.get("history_server") or ""
    if base_url:
        rows = fetch_remote(
            base_url, token, device_id, hours,
            settings_dict.get("history_timeout", 3),
        )
        if rows:
            return rows, "server"
        if rows is not None:
            # The server answered with nothing. That happens right after
            # deployment, before the collector has rows for this device. Prefer
            # local data if there is any, rather than drawing an empty chart.
            local = local_history(device_id, hours)
            return (local, "local") if local else ([], "server")

    return local_history(device_id, hours), "local"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python3 -m pytest tests/test_history_client.py -v`
Expected: 3 passed

- [ ] **Step 5: Wire it into the widget entry point**

In `tuya_client.py`, replace the `history` branch inside `__main__`:

```python
    # History mode: output chart data and exit
    if mode == "history":
        device_id = args[1] if len(args) > 1 else ""
        hours = int(args[2]) if len(args) > 2 else 1
        app_settings = load_settings()
        token = (load_config() or {}).get("history_token", "")
        history, source = history_client.get_history(
            app_settings, token, device_id, hours
        )
        print(json.dumps({
            "history": history,
            "history_device": device_id,
            "history_source": source,
        }))
        sys.exit(0)
```

and add the import at the top:

```python
import history_client
```

- [ ] **Step 6: Shrink the local cache**

With the server as the single source of truth, keeping a full day locally means
rewriting a 440 KB JSON file every 7 seconds for data nobody reads. Six hours is
enough to cover the 1h and 6h chart buttons during a server outage.

In `tuya_history.py`, replace the constant:

```python
# Emergency cache only -- the server is the source of truth. Sized in hours
# rather than entries because the poll interval has changed before: the old
# 8640 was labelled "24h at 10s" but the widget polls every 7s, so it actually
# held about 17 hours and the 24h chart button quietly showed less than a day.
LOCAL_HISTORY_HOURS = 6
MAX_ENTRIES_PER_DEVICE = LOCAL_HISTORY_HOURS * 3600 // 5  # 5s floor on polling
```

- [ ] **Step 7: Show which source the chart came from**

In `contents/ui/main.qml`, add the property next to the other chart properties
(near line 24):

```qml
    property string chartSource: "server"
```

In the `onNewData` handler, next to the existing history branch:

```qml
                    if (result.history !== undefined) {
                        // Only update chart if data is for the currently selected device
                        if (!result.history_device || result.history_device === chartDeviceId) {
                            chartData = result.history
                            chartSource = result.history_source || "server"
                            chartCanvas.requestPaint()
                        }
                    }
```

Add a warning label inside the chart area, directly above the period selector
`RowLayout` (near line 515):

```qml
                        PlasmaComponents.Label {
                            Layout.alignment: Qt.AlignHCenter
                            visible: chartSource === "local"
                            text: "локальные данные, сервер недоступен"
                            font.pixelSize: 10
                            color: "#fbbf24"
                        }
```

- [ ] **Step 8: Verify the fallback path with no server configured**

Run: `./venv/bin/python3 tuya_client.py history bf973442af478b5404fupa 1`
Expected: the same chart rows as before, plus `"history_source": "local"`

- [ ] **Step 9: Check the QML edit without installing it**

Do **not** copy the QML into the plasmoid and do **not** restart plasmashell.
Two reasons: this work happens in a git worktree while `main.qml` hardcodes
`/home/charoyan/projects/tuya/...`, so an installed copy would run the main
checkout's Python and prove nothing; and restarting the shell disturbs a
desktop that is in use. The owner installs and eyeballs it after the merge.

Verify the edit is syntactically valid instead:

```bash
qmllint contents/ui/main.qml 2>&1 | grep -v "^Warning.*import" || true
```

If `qmllint` is not installed, check the braces balance instead:

```bash
./venv/bin/python3 -c "
src = open('contents/ui/main.qml').read()
assert src.count('{') == src.count('}'), 'unbalanced braces'
assert 'chartSource' in src and 'history_source' in src
print('QML edit looks structurally sound')
"
```

Expected: no syntax complaints, and the check prints its confirmation.

- [ ] **Step 10: Commit**

```bash
git add history_client.py tests/test_history_client.py tuya_client.py tuya_history.py contents/ui/main.qml
git commit -m "Read chart history from the server, showing when the local fallback is used"
```

---

### Task 9: Import the existing history

**Files:**
- Create: `server/import_legacy.py`, `tests/test_import_legacy.py`

**Interfaces:**
- Consumes: `storage.connect`, `storage.write`.
- Produces: `import_legacy.import_file(conn, path) -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_import_legacy.py`:

```python
import json

import pytest
from server import import_legacy, storage


@pytest.fixture
def conn(tmp_path):
    c = storage.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_imports_power_and_voltage(conn, tmp_path):
    path = tmp_path / "power_history.json"
    path.write_text(json.dumps({"dev1": [[100, 90.9, 236.0], [110, 80.0, 235.0]]}))
    assert import_legacy.import_file(conn, path) == 4
    assert storage.series(conn, "dev1", "power", 0) == [(100, 90.9), (110, 80.0)]
    assert storage.series(conn, "dev1", "voltage", 0) == [(100, 236.0), (110, 235.0)]


def test_entry_without_voltage_still_imports_power(conn, tmp_path):
    path = tmp_path / "power_history.json"
    path.write_text(json.dumps({"dev1": [[100, 90.9]]}))
    assert import_legacy.import_file(conn, path) == 1
    assert storage.series(conn, "dev1", "power", 0) == [(100, 90.9)]


def test_missing_file_imports_nothing(conn, tmp_path):
    assert import_legacy.import_file(conn, tmp_path / "nope.json") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python3 -m pytest tests/test_import_legacy.py -v`
Expected: FAIL with `ImportError: cannot import name 'import_legacy'`

- [ ] **Step 3: Write the implementation**

Create `server/import_legacy.py`:

```python
#!/usr/bin/env python3
"""
One-off import of the widget's power_history.json into SQLite, so the chart
does not lose the history collected before the server existed.

    ./venv/bin/python3 -m server.import_legacy power_history.json readings.db
"""
import json
import sys
from pathlib import Path

from server import storage


def import_file(conn, path):
    """Returns the number of rows written."""
    path = Path(path)
    if not path.exists():
        return 0

    history = json.loads(path.read_text())
    written = 0
    for device_id, entries in history.items():
        for entry in entries:
            if len(entry) < 2:
                continue
            values = {"power": float(entry[1])}
            if len(entry) > 2:
                values["voltage"] = float(entry[2])
            written += storage.write(conn, int(entry[0]), device_id, values)
    return written


if __name__ == "__main__":
    source, database = sys.argv[1], sys.argv[2]
    connection = storage.connect(database)
    print(f"imported {import_file(connection, source)} rows")
    connection.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python3 -m pytest tests/test_import_legacy.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add server/import_legacy.py tests/test_import_legacy.py
git commit -m "Add one-off import of the legacy history file"
```

---

### Task 10: Deployment

**Files:**
- Create: `pytest.ini`, `server/systemd/tuya-collector.service`, `server/systemd/tuya-api.service`, `server/README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing consumed by other tasks.

- [ ] **Step 1: Stop bare `pytest` from detonating**

`test_region.py` and `test_statistics.py` sit at the repository root. They are
hand-run diagnostic scripts, not pytest tests, and `test_statistics.py` calls
`exit(1)` at import time — so a bare `pytest` from the root dies with
INTERNALERROR before running anything. Every command in this plan passes
`tests/` explicitly and so dodges it, but the next person will not know that.

Create `pytest.ini`:

```ini
[pytest]
testpaths = tests
```

Verify both invocations now work:

```bash
./venv/bin/python3 -m pytest -q
./venv/bin/python3 -m pytest tests/ -q
```

Expected: both collect the same tests and pass. Neither reports INTERNALERROR.

- [ ] **Step 2: Run the whole suite**

Run: `./venv/bin/python3 -m pytest tests/ -v`
Expected: all tests pass, 55 total

- [ ] **Step 3: Write the collector unit**

Create `server/systemd/tuya-collector.service`:

```ini
[Unit]
Description=Tuya collector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tuya
WorkingDirectory=/opt/tuya
ExecStart=/opt/tuya/venv/bin/python3 -m server.collector
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Write the API unit**

Create `server/systemd/tuya-api.service`:

```ini
[Unit]
Description=Tuya history API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tuya
WorkingDirectory=/opt/tuya
ExecStart=/opt/tuya/venv/bin/python3 -m server.api
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Write the deployment guide**

Create `server/README.md`:

````markdown
# Collector on the Oracle server

## Identify the machine first

```bash
cat /etc/os-release
```

Oracle Linux uses `firewalld`, Ubuntu and Debian use `ufw`. The commands below
show both.

## Install

```bash
sudo useradd -r -m -d /opt/tuya tuya
sudo -u tuya git clone <repo-url> /opt/tuya
cd /opt/tuya
sudo -u tuya python3 -m venv venv
sudo -u tuya ./venv/bin/pip install -r requirements.txt
```

## Configure

```bash
sudo -u tuya cp settings.json.example settings.json
sudo -u tuya cp config.json.example config.json
```

Edit `settings.json`: set `server.database` to `/opt/tuya/readings.db`.
Edit `config.json`: fill in the device ids, and generate the API token:

```bash
./venv/bin/python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put that value in `config.json` as `history_token`, and the same value in the
desktop's `config.json`.

## Log in to Smart Life

Run this **on the server**, not by copying `sharing_token.json` from the
desktop. The refresh token rotates, and two clients sharing one session evict
each other.

```bash
sudo -u tuya ./venv/bin/python3 tuya_auth.py
```

## Import the old history (optional, once)

Copy `power_history.json` from the desktop, then:

```bash
sudo -u tuya ./venv/bin/python3 -m server.import_legacy power_history.json readings.db
```

## Start the services

```bash
sudo cp server/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tuya-collector tuya-api
journalctl -u tuya-collector -f
```

## Open the port

Two places, and the second is the one people forget. Oracle images ship with
iptables rules that drop everything except port 22, so opening the Security
List alone is not enough.

1. Oracle console: VCN → Security List → add an ingress rule for TCP 8080.
2. On the machine:

```bash
# Oracle Linux
sudo firewall-cmd --permanent --add-port=8080/tcp && sudo firewall-cmd --reload

# Ubuntu / Debian
sudo ufw allow 8080/tcp
```

Check from the desktop:

```bash
curl -H "Authorization: Bearer <token>" \
  "http://<server-ip>:8080/history?device=<socket-id>&hours=1"
```

## Nightly retention

```bash
sudo -u tuya crontab -e
```

```
17 4 * * * /opt/tuya/venv/bin/python3 -m server.prune --database /opt/tuya/readings.db --older-than-days 365 --yes
```

## Deleting a period by hand

Dry run first — this is the default:

```bash
./venv/bin/python3 -m server.prune --database readings.db \
  --from 2026-09-01 --to 2026-09-03
```

Add `--yes` to actually delete, `--device` or `--metric` to narrow it.
````

- [ ] **Step 6: Point AGENTS.md at the server**

Append to the "Repository layout" section of `AGENTS.md`:

```markdown
- `server/`
  - Collector and history API that run on the Oracle box, not on the desktop.
  - See `server/README.md` for deployment. Design rationale and the
    measurements behind it are in
    `docs/superpowers/specs/2026-09-08-oracle-monitoring-design.md`.
- `settings.py`, `settings.json`
  - Behaviour settings, kept apart from the secrets in `config.json`.
  - `settings.json` is gitignored; `settings.json.example` is the template.
```

- [ ] **Step 7: Commit**

```bash
git add pytest.ini server/systemd server/README.md AGENTS.md
git commit -m "Add systemd units and server deployment guide"
```

---

## Self-Review

**Spec coverage.** Collector with push and polling → Task 6. Read-only API → Task 7. Widget change with fallback → Task 8. SQLite storage and the narrow schema → Task 3. Human units from declared scales → Task 2. Retention of 365 days → Task 4 and the cron entry in Task 10. Manual prune with dry-run and VACUUM → Task 4. Config split by purpose → Task 1. Device roles detected rather than configured → Task 5. Legacy import → Task 9. Separate QR login on the server, `prefer_ipv4`, port opened in both places → Task 10. Every spec section maps to a task.

**Type consistency.** `storage.write(conn, ts, device, values)` is called with that argument order in Tasks 4, 6 and 9. `units.convert(code, value, scales)` returns a `(metric, value)` tuple or `None`, and both callers in Task 6 check for `None` before unpacking. `roles.classify` returns `(push, poll, scales)` and Task 6 unpacks exactly three values. `history_client.get_history(settings_dict, token, device_id, hours)` returns a `(rows, source)` tuple in its implementation, all five of its tests and the `tuya_client.py` call site.

**Fallback semantics.** An earlier draft returned the server's answer whenever the request itself succeeded, which would have blanked the chart for any device the collector had no rows for yet — a blank chart on the first day of deployment, with usable local data sitting right there. `get_history` now falls back on an empty answer too, and only reports `"server"` for an empty result when the local cache is empty as well, so the widget does not warn about a fallback that did not happen.

**Known deviation from the spec.** The spec's `settings.json` table lists a log level; the plan drops it because both services log through journald, which already has level filtering. Nothing else in the spec is unimplemented.

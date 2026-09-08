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

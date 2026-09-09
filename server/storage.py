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


def series_bucketed(conn, device, metric, since_ts, bucket_seconds, until_ts=None):
    """
    [[bucket_start_ts, average_value], ...] ordered by time, one entry per
    bucket that has rows.

    Grouping happens in SQL via integer division of ts by bucket_seconds --
    SQLite's "/" truncates for two integer operands, which is exactly the
    bucket index. Each entry is labelled with the bucket's start
    (bucket_index * bucket_seconds), not the timestamp of whichever row
    happened to land in it first: labelling by first-row-ts would make the
    x position of a bucket drift with which sample arrived first, instead of
    sitting on a fixed hourly (or whatever bucket_seconds is) grid. A bucket
    with no rows is simply absent from the result -- GROUP BY never
    manufactures a row for a group that does not exist, so there is no zero
    to filter out.
    """
    bucket_seconds = int(bucket_seconds)
    if bucket_seconds <= 0:
        # SQLite returns NULL for division by zero rather than raising, so a
        # bucket of 0 would collapse every row into one entry labelled with a
        # NULL timestamp -- which serialises to null, reaches the chart as NaN
        # and draws nothing, with no error anywhere to explain it. The caller
        # in api.py already routes 0 to the unbucketed query; this refuses the
        # value outright so the next caller cannot get the silent version.
        raise ValueError("bucket_seconds must be positive")
    sql = (
        "SELECT (ts / ?) * ? AS bucket_start, AVG(value) FROM readings "
        "WHERE device = ? AND metric = ? AND ts >= ?"
    )
    args = [bucket_seconds, bucket_seconds, device, metric, since_ts]
    if until_ts is not None:
        sql += " AND ts <= ?"
        args.append(until_ts)
    sql += " GROUP BY bucket_start ORDER BY bucket_start"
    return [[row[0], row[1]] for row in conn.execute(sql, args)]


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

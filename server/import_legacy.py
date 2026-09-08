#!/usr/bin/env python3
"""
One-off import of the widget's power_history.json into SQLite, so the chart
does not lose the history collected before the server existed.

WARNING: Re-running this importer against an already-populated database will
silently duplicate every historical row. The database has no uniqueness
constraint, and the importer inserts unconditionally. This is hard to detect
and recover from (requires manual pruning). The guard below refuses to import
if it detects existing rows for any device in the source file, unless you
pass --force to skip the check.

    ./venv/bin/python3 -m server.import_legacy power_history.json readings.db
    ./venv/bin/python3 -m server.import_legacy power_history.json readings.db --force
"""
import argparse
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


def check_for_existing_data(conn, path):
    """
    Check if the database already has rows for any device in the source file.
    Returns a dict of {device_id: row_count} for devices that already exist.
    Returns empty dict if database is empty or no conflicts.
    """
    path = Path(path)
    if not path.exists():
        return {}

    history = json.loads(path.read_text())
    conflicts = {}

    for device_id in history.keys():
        # Use count_range to check if device already has rows
        # Count from time 0 to a far future time to catch all existing data
        count_dict = storage.count_range(conn, 0, 2**31 - 1, device=device_id)
        if device_id in count_dict and count_dict[device_id] > 0:
            conflicts[device_id] = count_dict[device_id]

    return conflicts


def main():
    parser = argparse.ArgumentParser(
        description="Import legacy power_history.json into SQLite"
    )
    parser.add_argument("source", help="Path to legacy power_history.json file")
    parser.add_argument("database", help="Path to SQLite database file")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the check for existing data (allows duplicating rows)",
    )

    args = parser.parse_args()

    connection = storage.connect(args.database)

    # Check for existing data unless --force is given
    if not args.force:
        conflicts = check_for_existing_data(connection, args.source)
        if conflicts:
            print(
                "Error: Database already contains rows for these devices:",
                file=sys.stderr,
            )
            for device_id, count in sorted(conflicts.items()):
                print(f"  {device_id}: {count} rows", file=sys.stderr)
            print(
                "This would duplicate the data. Use --force to skip this check if you know what you are doing.",
                file=sys.stderr,
            )
            connection.close()
            sys.exit(1)

    rows = import_file(connection, args.source)
    print(f"imported {rows} rows")
    connection.close()


if __name__ == "__main__":
    main()

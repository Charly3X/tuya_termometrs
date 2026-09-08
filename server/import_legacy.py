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

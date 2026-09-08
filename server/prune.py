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

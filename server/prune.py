#!/usr/bin/env python3
"""
Delete history: either a period chosen by hand, or everything past the
retention age. Both paths compute a (start, end) bound and hand it to the
same storage.delete_range(), so they cannot drift apart.

    prune.py --database DB --from 2026-09-01 --to 2026-09-03 [--device ID] [--metric power]
    prune.py --database DB --yes                       # retention from settings.json
    prune.py --database DB --older-than-days 365 --yes # retention, overridden
    prune.py --database DB --older-than-days 90 --yes --vacuum # retention, reclaim disk too

Without --yes nothing is deleted; the tool only reports what would go.

Only the by-hand range vacuums afterwards by default. That is the "this data
is garbage, give me the disk back" case. The retention run deliberately does
not: in steady state deletions balance insertions so there is nothing to
reclaim, and a VACUUM of a multi-gigabyte database rewrites the whole file
under an exclusive lock, during which the collector's writes hit the busy
timeout and are lost.

`--vacuum` overrides that default and forces a vacuum regardless of which
selection mode was used. This exists because lowering `retention_days` is
the obvious response to a full disk, but a retention delete alone never
shrinks the file -- the freed pages go to SQLite's freelist for reuse, not
back to the filesystem. Use it deliberately (ideally at a quiet moment): it
carries the same exclusive-lock cost described above.
"""
import argparse
import sys
import time
from datetime import datetime, timezone

from server import storage
from settings import load_settings


def parse_day(text):
    """Unix timestamp of midnight UTC on the given YYYY-MM-DD."""
    day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(day.timestamp())


def day_bounds(from_text, to_text):
    """(start, end) covering both days completely, end inclusive."""
    return parse_day(from_text), parse_day(to_text) + 86400 - 1


def older_than_bounds(days, now=None):
    """(start, end) covering everything older than `days`, end inclusive."""
    now = int(now if now is not None else time.time())
    return 0, now - days * 86400


def main(argv=None, settings_dict=None):
    parser = argparse.ArgumentParser(description="Delete Tuya history")
    parser.add_argument("--database", required=True)
    parser.add_argument("--from", dest="from_day", help="YYYY-MM-DD, inclusive")
    parser.add_argument("--to", dest="to_day", help="YYYY-MM-DD, inclusive")
    parser.add_argument(
        "--older-than-days",
        type=int,
        help="default: server.retention_days from settings.json",
    )
    parser.add_argument("--device")
    parser.add_argument("--metric")
    parser.add_argument("--yes", action="store_true", help="actually delete")
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="force a VACUUM afterwards, even on the retention path "
        "(default: only --from/--to vacuums)",
    )
    args = parser.parse_args(argv)

    by_hand = bool(args.from_day or args.to_day)
    if by_hand:
        if not (args.from_day and args.to_day):
            parser.error("--from and --to must be given together")
        if args.older_than_days is not None:
            parser.error("give either --older-than-days or --from/--to, not both")
        start, end = day_bounds(args.from_day, args.to_day)
    else:
        # No flag means "whatever settings.json says". Before this, retention
        # lived only in the README's cron line, so editing retention_days in
        # settings.json silently did nothing.
        days = args.older_than_days
        if days is None:
            days = (settings_dict or load_settings())["server"]["retention_days"]
        start, end = older_than_bounds(days)

    conn = storage.connect(args.database)
    try:
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
        if by_hand or args.vacuum:
            storage.vacuum(conn)
            print(f"\nDeleted {deleted} rows and vacuumed.")
        else:
            # No VACUUM on the nightly path -- see the module docstring.
            print(f"\nDeleted {deleted} rows.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

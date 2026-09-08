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
import math
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from server import storage

log = logging.getLogger("api")


def make_handler(conn, token, retention_days=365):
    # A falsy token (empty string, None) would make compare_digest("", "")
    # true for a bare "Authorization: Bearer " header, authenticating
    # anyone. A config file with an empty history_token is a plausible
    # accident (unfilled placeholder, bad edit, truncated deploy), so this
    # must fail closed at startup rather than silently accept everyone.
    if not token:
        raise ValueError(
            "api.make_handler: token is empty. Set a non-empty "
            "history_token in config.json — refusing to start a server "
            "that would authenticate any request."
        )

    # ThreadingHTTPServer answers each request on its own thread, but the
    # sqlite3 connection we're handed was opened on the thread that called
    # storage.connect() and refuses to be touched from any other thread
    # (check_same_thread, on by default). Reading the underlying file path
    # here — while we're still on that original thread — lets each request
    # open its own short-lived connection to the same database instead.
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

    # Nothing older than the retention age survives the nightly prune, so a
    # larger window can only ever mean "everything there is" — and at steady
    # state "everything" for one socket is ~7M rows: a multi-gigabyte Python
    # list plus a ~200 MB JSON string, built in memory on a free-tier VM,
    # per request, with no concurrency limit. One request would OOM the box
    # and take the collector down with it. Clamping costs nothing, because
    # the clamped window still covers every row that exists.
    max_hours = retention_days * 24

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # HTTP/1.1 means keep-alive, and without a timeout an
        # unauthenticated client can open a connection to this
        # internet-facing port, send nothing, and hold a thread forever.
        # A handful of those exhaust the box. socketserver applies this to
        # the connection socket, and BaseHTTPRequestHandler turns the
        # resulting timeout into a closed connection.
        timeout = 10

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

            # Untrusted network input: anything non-numeric, non-positive,
            # or non-finite must not be allowed to raise inside this
            # handler (an unhandled exception here kills the request
            # thread and leaves the client with a bare connection drop,
            # not an answer). float() happily parses "nan"/"inf"/
            # "infinity", and those survive a bare "> 0" check (nan
            # compares False against everything, +inf is > 0), so
            # math.isfinite() is checked explicitly. A merely absurd but
            # finite value is then clamped to the retention window rather
            # than rejected, so an over-eager client still gets an answer
            # — just not the whole database in one response. The overflow
            # guard stays because max_hours is itself config-derived: a
            # nonsense retention_days can still push hours * 3600 to +inf
            # (float multiplication overflows silently in Python, it
            # doesn't raise), so the guard checks the actual value that
            # governs the rest of the handler — the computed "since" —
            # and not just "hours" in isolation.
            raw_hours = (query.get("hours") or ["24"])[0]
            try:
                hours = float(raw_hours)
                if not math.isfinite(hours) or hours <= 0:
                    raise ValueError("hours must be a finite positive number")
                hours = min(hours, max_hours)
                since_float = time.time() - hours * 3600
                if not math.isfinite(since_float):
                    raise ValueError("hours is too large to compute a time bound")
            except ValueError:
                self._send(400, {"error": "hours must be a positive number"})
                return

            # A finite "since" can still be too large in magnitude for
            # SQLite's 64-bit signed INTEGER column (storage.py binds it
            # straight into the query), which raises OverflowError deep
            # in storage.series. Clamping the lower bound to
            # the Unix epoch keeps the "absurdly large hours means return
            # everything" behaviour from before — no real reading has a
            # timestamp earlier than 1970 — without ever handing SQLite a
            # value it can't represent.
            since = max(int(since_float), 0)

            request_conn = sqlite3.connect(db_path, timeout=30)
            try:
                if url.path == "/series":
                    metric = (query.get("metric") or ["power"])[0]
                    rows = storage.series(request_conn, device, metric, since)
                    self._send(200, [[ts, value] for ts, value in rows])
                else:
                    self._send(404, {"error": "not found"})
            finally:
                request_conn.close()

        def log_message(self, fmt, *args):
            log.info(fmt, *args)

    return Handler


def serve(conn, token, port, retention_days=365):
    handler = make_handler(conn, token, retention_days)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
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
        # .get, not [], so a config.json without the key reaches
        # make_handler's guard and the operator sees its explanation
        # instead of a bare KeyError traceback under systemd.
        config.get("history_token", ""),
        app_settings["server"]["port"],
        app_settings["server"]["retention_days"],
    )

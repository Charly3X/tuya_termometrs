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
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from server import storage

log = logging.getLogger("api")


def make_handler(conn, token):
    # ThreadingHTTPServer answers each request on its own thread, but the
    # sqlite3 connection we're handed was opened on the thread that called
    # storage.connect() and refuses to be touched from any other thread
    # (check_same_thread, on by default). Reading the underlying file path
    # here — while we're still on that original thread — lets each request
    # open its own short-lived connection to the same database instead.
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

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

            request_conn = sqlite3.connect(db_path)
            try:
                if url.path == "/history":
                    self._send(200, storage.power_series(request_conn, device, since))
                elif url.path == "/series":
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

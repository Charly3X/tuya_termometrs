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

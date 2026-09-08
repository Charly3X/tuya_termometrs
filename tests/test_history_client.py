import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import history_client

SERVER_ROWS = [[100, 23.6], [110, 23.7]]
LOCAL_ROWS = [[100, 90.9], [110, 80.0]]


@pytest.fixture
def server_url():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            body = json.dumps(SERVER_ROWS).encode()
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


@pytest.fixture
def local_rows(monkeypatch):
    monkeypatch.setattr(history_client, "local_power", lambda d, h: list(LOCAL_ROWS))


def test_server_answer_is_used(server_url, local_rows):
    series, source, summary = history_client.get_series(
        {"history_server": server_url, "history_timeout": 3}, "tok", "dev", 1,
        ["temperature"],
    )
    assert series == {"temperature": SERVER_ROWS}
    assert source == "server"
    assert summary is None


def test_several_metrics_are_fetched_together(server_url, local_rows):
    series, source, _ = history_client.get_series(
        {"history_server": server_url, "history_timeout": 3}, "tok", "dev", 1,
        ["temperature", "humidity"],
    )
    assert set(series) == {"temperature", "humidity"}
    assert source == "server"


def test_power_falls_back_to_the_local_file(local_rows):
    series, source, summary = history_client.get_series(
        {"history_server": "http://127.0.0.1:1", "history_timeout": 1},
        "tok", "dev", 1, ["power"],
    )
    assert series == {"power": LOCAL_ROWS}
    assert source == "local"
    assert summary is not None


def test_temperature_has_no_local_fallback(local_rows):
    """
    The local file only ever held power and voltage. Reporting "local" here
    would promise data that does not exist.
    """
    series, source, summary = history_client.get_series(
        {"history_server": "http://127.0.0.1:1", "history_timeout": 1},
        "tok", "dev", 1, ["temperature", "humidity"],
    )
    assert series == {}
    assert source == "unavailable"
    assert summary is None


def test_no_server_configured_still_serves_power_locally(local_rows):
    series, source, _ = history_client.get_series(
        {"history_server": "", "history_timeout": 3}, "tok", "dev", 1, ["power"],
    )
    assert series == {"power": LOCAL_ROWS}
    assert source == "local"


def test_empty_server_answer_is_treated_as_no_data(local_rows, monkeypatch):
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: [])
    series, source, _ = history_client.get_series(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", "dev", 1, ["power"],
    )
    assert series == {"power": LOCAL_ROWS}
    assert source == "local"


def test_empty_everywhere_reports_unavailable(monkeypatch):
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: [])
    monkeypatch.setattr(history_client, "local_power", lambda d, h: [])
    series, source, summary = history_client.get_series(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", "dev", 1, ["power"],
    )
    assert series == {}
    assert source == "unavailable"
    assert summary is None


def test_series_are_downsampled(monkeypatch):
    monkeypatch.setattr(
        history_client, "fetch_series",
        lambda *a, **k: [[i, float(i)] for i in range(12000)],
    )
    series, source, _ = history_client.get_series(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", "dev", 24, ["power"],
    )
    assert source == "server"
    assert len(series["power"]) < 12000


def test_summary_is_computed_before_downsampling(monkeypatch):
    """
    The reason get_series returns a summary at all instead of leaving it to
    the caller. This series is 10% duty: 90 W for one sample in ten, 0 W
    otherwise, so the true mean is 9 W. Downsampling keeps each bucket's min
    and max, collapsing it to an alternating 0/90 series whose mean is 45 W.
    Summarising the drawn data would overstate consumption fivefold.
    """
    raw = [[i, 90.0 if i % 10 == 0 else 0.0] for i in range(12000)]
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: raw)
    series, source, summary = history_client.get_series(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", "dev", 24, ["power"],
    )
    assert len(series["power"]) < 12000       # it really was downsampled
    assert abs(summary["avg"] - 9.0) < 0.5    # but the mean came from the raw rows

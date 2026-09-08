import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import chart_data
import history_client

SERVER_ROWS = [[100, 23.6], [110, 23.7]]
LOCAL_ROWS = [[100, 90.9], [110, 80.0]]
SERVER_SUMMARY = {"min": 1.0, "avg": 2.0, "max": 3.0, "kwh": 4.0}


@pytest.fixture
def server_url():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            if self.path.startswith("/summary"):
                body = json.dumps(SERVER_SUMMARY).encode()
            else:
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
    series, source, summaries = history_client.get_series(
        {"history_server": server_url, "history_timeout": 3}, "tok", "dev", 1,
        ["temperature"],
    )
    assert series == {"temperature": SERVER_ROWS}
    assert source == "server"
    assert summaries == {"temperature": SERVER_SUMMARY}


def test_several_metrics_are_fetched_together(server_url, local_rows):
    series, source, summaries = history_client.get_series(
        {"history_server": server_url, "history_timeout": 3}, "tok", "dev", 1,
        ["temperature", "humidity"],
    )
    assert set(series) == {"temperature", "humidity"}
    assert source == "server"
    assert set(summaries) == {"temperature", "humidity"}


def test_power_falls_back_to_the_local_file(local_rows):
    series, source, summaries = history_client.get_series(
        {"history_server": "http://127.0.0.1:1", "history_timeout": 1},
        "tok", "dev", 1, ["power"],
    )
    assert series == {"power": LOCAL_ROWS}
    assert source == "local"
    assert summaries == {"power": chart_data.summarise_power(LOCAL_ROWS)}


def test_temperature_has_no_local_fallback(local_rows):
    """
    The local file only ever held power and voltage. Reporting "local" here
    would promise data that does not exist.
    """
    series, source, summaries = history_client.get_series(
        {"history_server": "http://127.0.0.1:1", "history_timeout": 1},
        "tok", "dev", 1, ["temperature", "humidity"],
    )
    assert series == {}
    assert source == "unavailable"
    assert summaries == {}


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


def test_server_answered_with_nothing_reports_empty(monkeypatch):
    """
    The collector answered, it just has not recorded this device yet. Saying
    "unavailable" here would send the user to debug a server that is alive,
    and the spec's own fallback table ends a plug with no data anywhere at
    "нет данных", not at "сервер недоступен".
    """
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: [])
    monkeypatch.setattr(history_client, "local_power", lambda d, h: [])
    series, source, summaries = history_client.get_series(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", "dev", 1, ["power"],
    )
    assert series == {}
    assert source == "empty"
    assert summaries == {}


def test_unreachable_server_reports_unavailable(monkeypatch):
    """
    The other half of the pair: fetch_series returns None only when the
    request itself failed, and that is the one case worth naming the server
    for.
    """
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: None)
    monkeypatch.setattr(history_client, "local_power", lambda d, h: [])
    series, source, summaries = history_client.get_series(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", "dev", 1, ["power"],
    )
    assert series == {}
    assert source == "unavailable"
    assert summaries == {}


def test_no_server_configured_and_no_local_data_is_unavailable(monkeypatch):
    """An unconfigured server is still a server the widget cannot reach."""
    monkeypatch.setattr(history_client, "local_power", lambda d, h: [])
    _, source, _ = history_client.get_series(
        {"history_server": "", "history_timeout": 3}, "tok", "dev", 1, ["power"],
    )
    assert source == "unavailable"


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


def test_summary_comes_from_the_server_not_recomputed_locally(monkeypatch):
    """
    get_series must hand back whatever /summary answers rather than
    recomputing its own statistics from the (possibly downsampled) series it
    just drew -- that recomputation is exactly the bug this feature replaces
    (chart_data.downsample keeps each bucket's min/max, so a 10%-duty plug
    would summarise to a 45 W mean instead of the true 9 W).
    """
    raw = [[i, 90.0 if i % 10 == 0 else 0.0] for i in range(12000)]
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: raw)
    monkeypatch.setattr(
        history_client, "fetch_summary",
        lambda *a, **k: {"min": 0.0, "avg": 9.0, "max": 90.0, "kwh": 1.23},
    )
    series, source, summaries = history_client.get_series(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", "dev", 24, ["power"],
    )
    assert len(series["power"]) < 12000  # it really was downsampled
    assert summaries["power"] == {"min": 0.0, "avg": 9.0, "max": 90.0, "kwh": 1.23}


def test_fetch_summary_failure_leaves_the_metric_out_of_summaries(monkeypatch):
    """
    If /summary itself cannot be reached (while /series could), the widget
    should simply not show a summary for that metric rather than crash or
    invent one.
    """
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: SERVER_ROWS)
    monkeypatch.setattr(history_client, "fetch_summary", lambda *a, **k: None)
    series, source, summaries = history_client.get_series(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", "dev", 1, ["temperature"],
    )
    assert source == "server"
    assert summaries == {}


# -- get_energy --------------------------------------------------------

def test_get_energy_reports_null_for_a_device_with_no_data(monkeypatch):
    """
    /summary answers "no rows" and "genuinely zero" identically (both come
    back as all-zero statistics), so get_energy must not rely on /summary to
    tell them apart. It reads the raw series instead: an empty series means
    the collector has nothing recorded for this device, which is a different
    fact from "recorded and it was zero" and must not be reported as 0.0.
    """
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: [])
    energy, source = history_client.get_energy(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", ["dev-with-no-data"], 5,
    )
    assert energy == {"dev-with-no-data": None}
    assert source == "server"


def test_get_energy_integrates_the_raw_series_for_a_device_with_data(monkeypatch):
    raw = [[t, 100.0] for t in range(0, 3601, 60)]
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: raw)
    energy, source = history_client.get_energy(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", ["dev1"], 1,
    )
    assert abs(energy["dev1"] - 0.1) < 1e-9
    assert source == "server"


def test_get_energy_is_unavailable_with_no_server_configured():
    energy, source = history_client.get_energy(
        {"history_server": "", "history_timeout": 3}, "tok", ["dev1", "dev2"], 1,
    )
    assert energy == {"dev1": None, "dev2": None}
    assert source == "unavailable"


def test_get_energy_is_unavailable_when_the_server_cannot_be_reached(monkeypatch):
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: None)
    energy, source = history_client.get_energy(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", ["dev1"], 1,
    )
    assert energy == {"dev1": None}
    assert source == "unavailable"


def test_get_energy_reports_server_if_any_device_was_reached(monkeypatch):
    """
    One socket having nothing recorded should not make the whole command
    claim the collector is unreachable when another socket answered fine.
    """
    raw = [[0, 100.0], [60, 100.0]]

    def fake_fetch_series(base_url, token, device_id, hours, metric, timeout):
        return raw if device_id == "dev1" else None

    monkeypatch.setattr(history_client, "fetch_series", fake_fetch_series)
    energy, source = history_client.get_energy(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", ["dev1", "dev2"], 1,
    )
    assert energy["dev1"] is not None
    assert energy["dev2"] is None
    assert source == "server"

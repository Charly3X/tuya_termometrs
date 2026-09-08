import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from server import api, storage

TOKEN = "secret-token"


@pytest.fixture
def base_url(tmp_path):
    conn = storage.connect(tmp_path / "t.db")
    storage.write(conn, 1000, "dev1", {"power": 90.9, "voltage": 236.5})
    storage.write(conn, 1010, "dev1", {"power": 80.0})
    storage.write(conn, 1020, "dev2", {"temperature": 23.6})

    server = ThreadingHTTPServer(("127.0.0.1", 0), api.make_handler(conn, TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    conn.close()


def fetch(url, token=TOKEN):
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_history_returns_the_widget_shape(base_url):
    # hours must be large enough that "now - hours*3600" reaches back past
    # the fixture's near-epoch timestamps (1000, 1010) regardless of the
    # real wall-clock date the suite runs on; 99999 hours (~11 years) is
    # not enough once run more than ~11 years after 1970.
    result = fetch(f"{base_url}/history?device=dev1&hours=999999999")
    assert result == [[1000, 90.9, 236.5], [1010, 80.0, 236.5]]


def test_series_returns_pairs(base_url):
    result = fetch(f"{base_url}/series?device=dev2&metric=temperature&hours=999999999")
    assert result == [[1020, 23.6]]


def test_unknown_device_is_an_empty_list_not_an_error(base_url):
    assert fetch(f"{base_url}/history?device=nobody&hours=24") == []


def test_missing_token_is_rejected(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/history?device=dev1&hours=24", token=None)
    assert excinfo.value.code == 401


def test_wrong_token_is_rejected(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/history?device=dev1&hours=24", token="guess")
    assert excinfo.value.code == 401


def test_unknown_path_is_404(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/wat")
    assert excinfo.value.code == 404


def test_there_is_no_write_endpoint(base_url):
    request = urllib.request.Request(
        f"{base_url}/history?device=dev1&hours=24", data=b"{}", method="POST"
    )
    request.add_header("Authorization", f"Bearer {TOKEN}")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=5)
    assert excinfo.value.code == 501


def test_empty_token_refuses_to_start(tmp_path):
    # An empty history_token in config.json would make
    # compare_digest("", "") true for a bare "Authorization: Bearer "
    # header, authenticating anyone. The service must fail closed at
    # construction time rather than accept that per request.
    conn = storage.connect(tmp_path / "t.db")
    try:
        with pytest.raises(ValueError):
            api.make_handler(conn, "")
    finally:
        conn.close()


def test_non_numeric_hours_is_400(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/history?device=dev1&hours=abc")
    assert excinfo.value.code == 400


def test_zero_hours_is_400(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/history?device=dev1&hours=0")
    assert excinfo.value.code == 400


@pytest.mark.parametrize("value", ["nan", "inf", "infinity", "-inf"])
def test_non_finite_hours_is_400(base_url, value):
    # float() happily parses all of these, and they survive a bare
    # "> 0" check (nan compares False against everything, +inf is > 0),
    # so they must be rejected explicitly rather than relying on
    # whatever arithmetic happens to raise downstream.
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/history?device=dev1&hours={value}")
    assert excinfo.value.code == 400


@pytest.mark.parametrize("value", ["1e20", "99999999999999999999"])
def test_absurdly_large_but_finite_hours_still_works(base_url, value):
    # Large enough to push "since" far below any recorded timestamp
    # (and, before it was clamped, far below what SQLite's 64-bit
    # INTEGER column can hold), but nowhere near overflowing
    # hours * 3600 to +inf.
    result = fetch(f"{base_url}/history?device=dev1&hours={value}")
    assert result == [[1000, 90.9, 236.5], [1010, 80.0, 236.5]]


def test_hours_large_enough_to_overflow_since_is_400(base_url):
    # hours * 3600 overflows float64 to +inf for large enough finite
    # input (Python float multiplication overflows silently, it doesn't
    # raise), which would otherwise reach int() as -inf and raise
    # OverflowError deep in the handler.
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/history?device=dev1&hours=1e307")
    assert excinfo.value.code == 400


def test_absent_hours_defaults_to_24_and_returns_data(tmp_path):
    conn = storage.connect(tmp_path / "recent.db")
    now = int(time.time())
    storage.write(conn, now - 60, "dev1", {"power": 42.0, "voltage": 230.0})

    server = ThreadingHTTPServer(("127.0.0.1", 0), api.make_handler(conn, TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = fetch(f"http://127.0.0.1:{server.server_port}/history?device=dev1")
        assert result == [[now - 60, 42.0, 230.0]]
    finally:
        server.shutdown()
        conn.close()

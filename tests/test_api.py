import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from server import api, storage

TOKEN = "secret-token"

# The fixture's rows sit just after the epoch, so the fixture server is
# given a retention long enough that the "hours" clamp still reaches them.
# Tests that are about the clamp itself build their own server below.
FIXTURE_RETENTION_DAYS = 100_000


@pytest.fixture
def base_url(tmp_path):
    conn = storage.connect(tmp_path / "t.db")
    storage.write(conn, 1000, "dev1", {"power": 90.9, "voltage": 236.5})
    storage.write(conn, 1010, "dev1", {"power": 80.0})
    storage.write(conn, 1020, "dev2", {"temperature": 23.6})

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        api.make_handler(conn, TOKEN, retention_days=FIXTURE_RETENTION_DAYS),
    )
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


def test_series_returns_pairs(base_url):
    result = fetch(f"{base_url}/series?device=dev2&metric=temperature&hours=999999999")
    assert result == [[1020, 23.6]]


def test_missing_token_is_rejected(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/series?device=dev1&metric=power&hours=24", token=None)
    assert excinfo.value.code == 401


def test_wrong_token_is_rejected(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/series?device=dev1&metric=power&hours=24", token="guess")
    assert excinfo.value.code == 401


def test_unknown_path_is_404(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/wat")
    assert excinfo.value.code == 404


def test_there_is_no_write_endpoint(base_url):
    request = urllib.request.Request(
        f"{base_url}/series?device=dev1&metric=power&hours=24", data=b"{}", method="POST"
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
        fetch(f"{base_url}/series?device=dev1&metric=power&hours=abc")
    assert excinfo.value.code == 400


def test_zero_hours_is_400(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/series?device=dev1&metric=power&hours=0")
    assert excinfo.value.code == 400


@pytest.mark.parametrize("value", ["nan", "inf", "infinity", "-inf"])
def test_non_finite_hours_is_400(base_url, value):
    # float() happily parses all of these, and they survive a bare
    # "> 0" check (nan compares False against everything, +inf is > 0),
    # so they must be rejected explicitly rather than relying on
    # whatever arithmetic happens to raise downstream.
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/series?device=dev1&metric=power&hours={value}")
    assert excinfo.value.code == 400


@pytest.mark.parametrize("value", ["1e20", "99999999999999999999", "1e307"])
def test_absurdly_large_but_finite_hours_still_works(base_url, value):
    # These clamp to the retention window rather than being rejected; the
    # clamped window is still wide enough to cover every fixture row. Before
    # the clamp, 1e307 overflowed hours * 3600 to +inf and was a 400.
    result = fetch(f"{base_url}/series?device=dev1&metric=power&hours={value}")
    assert result == [[1000, 90.9], [1010, 80.0]]


def make_server(tmp_path, rows, retention_days):
    conn = storage.connect(tmp_path / "clamp.db")
    for ts, values in rows:
        storage.write(conn, ts, "dev1", values)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), api.make_handler(conn, TOKEN, retention_days=retention_days)
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, conn


def test_hours_is_clamped_to_the_retention_window(tmp_path):
    # Uncapped "hours" let one unauthenticated-cost request ask for the
    # whole database: ~7M rows for one socket at steady state, built as a
    # Python list and a JSON string in memory on a free-tier VM. Nothing
    # older than retention survives the nightly prune anyway, so clamping
    # loses no real data.
    now = int(time.time())
    server, conn = make_server(
        tmp_path,
        [(now - 10 * 86400, {"power": 1.0, "voltage": 230.0}),
         (now - 3600, {"power": 2.0})],
        retention_days=2,
    )
    try:
        result = fetch(f"http://127.0.0.1:{server.server_port}/series"
                       f"?device=dev1&metric=power&hours=999999999")
        assert result == [[now - 3600, 2.0]]
    finally:
        server.shutdown()
        conn.close()


def test_hours_within_the_retention_window_is_untouched(tmp_path):
    now = int(time.time())
    server, conn = make_server(
        tmp_path,
        [(now - 10 * 86400, {"power": 1.0, "voltage": 230.0}),
         (now - 3600, {"power": 2.0})],
        retention_days=365,
    )
    try:
        result = fetch(f"http://127.0.0.1:{server.server_port}/series"
                       f"?device=dev1&metric=power&hours=720")
        assert result == [[now - 10 * 86400, 1.0], [now - 3600, 2.0]]
    finally:
        server.shutdown()
        conn.close()


def test_a_retention_large_enough_to_overflow_since_is_400(tmp_path):
    # max_hours is config-derived, so the overflow guard still has to hold:
    # hours * 3600 overflows float64 to +inf for a large enough finite
    # value (Python float multiplication overflows silently, it doesn't
    # raise), which would otherwise reach int() as -inf and raise
    # OverflowError deep in the handler.
    server, conn = make_server(tmp_path, [(1000, {"power": 1.0})], retention_days=1e305)
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            fetch(f"http://127.0.0.1:{server.server_port}/series"
                  f"?device=dev1&metric=power&hours=1e307")
        assert excinfo.value.code == 400
    finally:
        server.shutdown()
        conn.close()


def test_handler_sets_a_connection_timeout():
    # HTTP/1.1 keep-alive with no timeout lets an unauthenticated client
    # open a connection to an internet-facing port, send nothing, and hold
    # a thread indefinitely.
    conn = storage.connect(":memory:")
    try:
        assert api.make_handler(conn, TOKEN).timeout == 10
    finally:
        conn.close()


def test_summary_returns_the_four_numbers_for_power(base_url):
    result = fetch(f"{base_url}/summary?device=dev1&metric=power&hours=999999999")
    assert set(result) == {"min", "avg", "max", "kwh"}
    assert result["min"] == 80.0
    assert result["max"] == 90.9
    assert result["kwh"] is not None


def test_summary_for_temperature_has_a_null_kwh(base_url):
    # Integrating a temperature curve does not mean anything, so kwh must be
    # None rather than a bogus number -- unlike power, which is always
    # integrable.
    result = fetch(f"{base_url}/summary?device=dev2&metric=temperature&hours=999999999")
    assert result["kwh"] is None
    assert result["min"] == 23.6
    assert result["max"] == 23.6


def test_summary_missing_token_is_rejected(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/summary?device=dev1&metric=power&hours=24", token=None)
    assert excinfo.value.code == 401


def test_summary_non_numeric_hours_is_400(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(f"{base_url}/summary?device=dev1&metric=power&hours=abc")
    assert excinfo.value.code == 400


def test_summary_for_unknown_device_is_zeros_not_an_error(base_url):
    result = fetch(f"{base_url}/summary?device=ghost&metric=power&hours=999999999")
    assert result == {"min": 0.0, "avg": 0.0, "max": 0.0, "kwh": 0.0}


def test_absent_hours_defaults_to_24_and_returns_data(tmp_path):
    conn = storage.connect(tmp_path / "recent.db")
    now = int(time.time())
    storage.write(conn, now - 60, "dev1", {"power": 42.0, "voltage": 230.0})

    server = ThreadingHTTPServer(("127.0.0.1", 0), api.make_handler(conn, TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = fetch(f"http://127.0.0.1:{server.server_port}/series?device=dev1&metric=power")
        assert result == [[now - 60, 42.0]]
    finally:
        server.shutdown()
        conn.close()

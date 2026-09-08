import json
import threading
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

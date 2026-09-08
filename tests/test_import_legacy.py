import json

import pytest
from server import import_legacy, storage


@pytest.fixture
def conn(tmp_path):
    c = storage.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_imports_power_and_voltage(conn, tmp_path):
    path = tmp_path / "power_history.json"
    path.write_text(json.dumps({"dev1": [[100, 90.9, 236.0], [110, 80.0, 235.0]]}))
    assert import_legacy.import_file(conn, path) == 4
    assert storage.series(conn, "dev1", "power", 0) == [(100, 90.9), (110, 80.0)]
    assert storage.series(conn, "dev1", "voltage", 0) == [(100, 236.0), (110, 235.0)]


def test_entry_without_voltage_still_imports_power(conn, tmp_path):
    path = tmp_path / "power_history.json"
    path.write_text(json.dumps({"dev1": [[100, 90.9]]}))
    assert import_legacy.import_file(conn, path) == 1
    assert storage.series(conn, "dev1", "power", 0) == [(100, 90.9)]


def test_missing_file_imports_nothing(conn, tmp_path):
    assert import_legacy.import_file(conn, tmp_path / "nope.json") == 0

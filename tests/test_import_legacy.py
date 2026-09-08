import json
import subprocess
import sys

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


def test_guard_detects_existing_data(conn, tmp_path):
    """The guard detects when a device already has rows in the database."""
    path = tmp_path / "power_history.json"
    path.write_text(json.dumps({"dev1": [[100, 90.9]]}))

    # First import succeeds
    assert import_legacy.import_file(conn, path) == 1

    # check_for_existing_data detects it
    conflicts = import_legacy.check_for_existing_data(conn, path)
    assert conflicts == {"dev1": 1}


def test_guard_ignores_unrelated_devices(conn, tmp_path):
    """The guard does not trip on devices in the database that are not in the source file."""
    path = tmp_path / "power_history.json"
    path.write_text(json.dumps({"dev1": [[100, 90.9]]}))

    # Import dev1
    import_legacy.import_file(conn, path)

    # Create a new source file with a different device
    other_path = tmp_path / "other_history.json"
    other_path.write_text(json.dumps({"dev2": [[200, 80.0]]}))

    # Guard should not report conflicts for dev2
    conflicts = import_legacy.check_for_existing_data(conn, other_path)
    assert conflicts == {}


def test_guard_returns_empty_dict_for_empty_database(conn, tmp_path):
    """The guard returns empty dict for a database with no rows."""
    path = tmp_path / "power_history.json"
    path.write_text(json.dumps({"dev1": [[100, 90.9]]}))

    # Database is empty
    conflicts = import_legacy.check_for_existing_data(conn, path)
    assert conflicts == {}


def test_guard_returns_empty_dict_for_missing_file(conn, tmp_path):
    """The guard returns empty dict if the source file doesn't exist."""
    conflicts = import_legacy.check_for_existing_data(conn, tmp_path / "nope.json")
    assert conflicts == {}


def test_cli_second_import_refuses(tmp_path):
    """Running the CLI twice against the same database refuses on the second run."""
    source = tmp_path / "power_history.json"
    source.write_text(json.dumps({"dev1": [[100, 90.9]]}))
    database = tmp_path / "readings.db"

    # First import succeeds
    result1 = subprocess.run(
        [
            sys.executable,
            "-m",
            "server.import_legacy",
            str(source),
            str(database),
        ],
        capture_output=True,
        text=True,
    )
    assert result1.returncode == 0
    assert "imported 1 rows" in result1.stdout

    # Second import fails
    result2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "server.import_legacy",
            str(source),
            str(database),
        ],
        capture_output=True,
        text=True,
    )
    assert result2.returncode != 0
    assert "already contains rows" in result2.stderr
    assert "dev1" in result2.stderr
    assert "--force" in result2.stderr


def test_cli_force_flag_skips_guard(tmp_path):
    """The --force flag allows re-importing despite existing data."""
    source = tmp_path / "power_history.json"
    source.write_text(json.dumps({"dev1": [[100, 90.9]]}))
    database = tmp_path / "readings.db"

    # First import
    result1 = subprocess.run(
        [
            sys.executable,
            "-m",
            "server.import_legacy",
            str(source),
            str(database),
        ],
        capture_output=True,
        text=True,
    )
    assert result1.returncode == 0

    # Second import with --force succeeds
    result2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "server.import_legacy",
            str(source),
            str(database),
            "--force",
        ],
        capture_output=True,
        text=True,
    )
    assert result2.returncode == 0
    assert "imported 1 rows" in result2.stdout


def test_cli_first_import_with_force_succeeds(tmp_path):
    """Using --force on the first import also works (it's just ignored)."""
    source = tmp_path / "power_history.json"
    source.write_text(json.dumps({"dev1": [[100, 90.9]]}))
    database = tmp_path / "readings.db"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "server.import_legacy",
            str(source),
            str(database),
            "--force",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "imported 1 rows" in result.stdout

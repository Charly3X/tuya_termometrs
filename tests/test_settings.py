import json
import settings


def test_missing_file_returns_defaults(tmp_path):
    result = settings.load_settings(tmp_path / "nope.json")
    assert result == settings.DEFAULTS
    assert result is not settings.DEFAULTS  # must be a copy, not the shared dict


def test_partial_override_keeps_other_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"history_timeout": 9}))
    result = settings.load_settings(path)
    assert result["history_timeout"] == 9
    assert result["cloud_backend"] == "sharing"


def test_nested_override_does_not_wipe_siblings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"server": {"port": 9999}}))
    result = settings.load_settings(path)
    assert result["server"]["port"] == 9999
    assert result["server"]["retention_days"] == 365


def test_defaults_are_not_mutated_by_a_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"server": {"port": 1}}))
    settings.load_settings(path)
    assert settings.DEFAULTS["server"]["port"] == 8080

from server import units

SOCKET_SCALES = {"cur_power": 1, "cur_voltage": 1, "cur_current": 0, "add_ele": 3}


def test_power_uses_declared_scale():
    assert units.convert("cur_power", 909, SOCKET_SCALES) == ("power", 90.9)


def test_energy_ignores_the_scale_tuya_declares():
    # Tuya declares scale 3 for add_ele, but this hardware reports 0.01 kWh
    # units. Measured 2026-09-08: raw 45 while the fridge had used ~0.45 kWh.
    # The override must win even when the specification is passed in.
    assert units.convert("add_ele", 45, {"add_ele": 3}) == ("energy", 0.45)


def test_scale_zero_passes_value_through():
    assert units.convert("cur_current", 435, SOCKET_SCALES) == ("current", 435.0)


def test_temperature_from_either_code_name():
    assert units.convert("va_temperature", 236, {"va_temperature": 1}) == ("temperature", 23.6)
    assert units.convert("temp_current", 244, {"temp_current": 1}) == ("temperature", 24.4)


def test_battery_enum_becomes_a_percentage():
    assert units.convert("battery_state", "middle", {}) == ("battery", 40.0)


def test_unknown_scale_falls_back_to_the_table():
    # A device with a broken product definition returns no specifications.
    metric, value = units.convert("cur_power", 909, {})
    assert (metric, value) == ("power", 90.9)


def test_untracked_code_is_ignored():
    assert units.convert("relay_status", "2", {}) is None


def test_boolean_switch_is_ignored_not_treated_as_one():
    assert units.convert("switch_1", True, {}) is None

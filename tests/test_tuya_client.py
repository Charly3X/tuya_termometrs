import tuya_client


def test_series_args_default_bucket_is_zero():
    # Existing three-argument invocations (device, hours, metrics) must keep
    # working unchanged: no fourth positional means no bucketing.
    assert tuya_client._parse_series_args(["series", "dev1", "24", "power"]) == (
        "dev1", 24, ["power"], 0,
    )


def test_series_args_reads_the_bucket_positional():
    assert tuya_client._parse_series_args(
        ["series", "dev1", "24", "temperature,humidity", "3600"]
    ) == ("dev1", 24, ["temperature", "humidity"], 3600)


def test_series_args_missing_metrics_default_to_power():
    assert tuya_client._parse_series_args(["series", "dev1", "24"]) == (
        "dev1", 24, ["power"], 0,
    )


def test_series_args_non_numeric_bucket_falls_back_to_zero():
    # Garbage here must not raise -- it degrades to "no bucketing" rather
    # than crashing the CLI call the widget is waiting on.
    assert tuya_client._parse_series_args(
        ["series", "dev1", "24", "power", "abc"]
    ) == ("dev1", 24, ["power"], 0)


def test_series_args_negative_bucket_falls_back_to_zero():
    assert tuya_client._parse_series_args(
        ["series", "dev1", "24", "power", "-5"]
    ) == ("dev1", 24, ["power"], 0)


def test_series_args_empty_device_id_shifts_every_later_positional():
    # The argument-shift trap: an empty device id disappears under shell
    # word splitting (main.qml builds the command by string concatenation),
    # so what was meant as hours/metrics/bucket lands one slot early. This
    # documents the actual (degraded) behaviour rather than pretending the
    # shift cannot happen -- validation is what keeps it from crashing, not
    # from occurring.
    assert tuya_client._parse_series_args(["series", "24", "power", "3600"]) == (
        "24", 0, ["3600"], 0,
    )


def test_series_args_no_positionals_at_all():
    assert tuya_client._parse_series_args(["series"]) == ("", 1, ["power"], 0)

import chart_data


def test_short_series_is_returned_unchanged():
    points = [[i, float(i)] for i in range(50)]
    assert chart_data.downsample(points, buckets=500) == points


def test_long_series_is_reduced():
    points = [[i, float(i % 7)] for i in range(12000)]
    result = chart_data.downsample(points, buckets=500)
    assert len(result) < len(points)
    assert len(result) <= 1000 + 1  # two per bucket, plus the forced last point


def test_downsampling_preserves_a_spike():
    """
    The whole reason for min/max bucketing. Taking every Nth sample would
    drop this point, and a consumption spike is what someone opens the
    chart to see.
    """
    points = [[i, 10.0] for i in range(12000)]
    points[5000] = [5000, 999.0]
    result = chart_data.downsample(points, buckets=500)
    assert any(p[1] == 999.0 for p in result)


def test_downsampling_preserves_a_dip():
    points = [[i, 10.0] for i in range(12000)]
    points[5000] = [5000, -3.0]
    result = chart_data.downsample(points, buckets=500)
    assert any(p[1] == -3.0 for p in result)


def test_downsampling_keeps_the_first_and_last_timestamps():
    points = [[i, float(i)] for i in range(12000)]
    result = chart_data.downsample(points, buckets=500)
    assert result[0][0] == 0
    assert result[-1][0] == 11999


def test_downsampled_output_is_time_ordered():
    points = [[i, float(i % 13)] for i in range(12000)]
    result = chart_data.downsample(points, buckets=500)
    stamps = [p[0] for p in result]
    assert stamps == sorted(stamps)


def test_downsampling_an_empty_series():
    assert chart_data.downsample([], buckets=500) == []


def test_summary_of_a_constant_hour():
    """100 W held for one hour is 0.1 kWh."""
    points = [[t, 100.0] for t in range(0, 3601, 60)]
    result = chart_data.summarise_power(points)
    assert result["min"] == 100.0
    assert result["max"] == 100.0
    assert result["avg"] == 100.0
    assert abs(result["kwh"] - 0.1) < 1e-9


def test_summary_integrates_a_changing_load():
    """Trapezoid: 0 W to 100 W over an hour averages 50 W, so 0.05 kWh."""
    points = [[0, 0.0], [3600, 100.0]]
    assert abs(chart_data.summarise_power(points)["kwh"] - 0.05) < 1e-9


def test_summary_skips_a_long_gap():
    """
    A gap means the collector was down, not that the load held steady.
    Integrating across five hours of silence would invent consumption.
    """
    points = [[0, 100.0], [3600, 100.0], [3600 + 20000, 100.0]]
    result = chart_data.summarise_power(points)
    assert abs(result["kwh"] - 0.1) < 1e-9


def test_summary_of_a_single_point_does_not_divide_by_zero():
    result = chart_data.summarise_power([[100, 42.0]])
    assert result == {"min": 42.0, "avg": 42.0, "max": 42.0, "kwh": 0.0}


def test_summary_of_nothing_is_all_zeros():
    assert chart_data.summarise_power([]) == {
        "min": 0.0, "avg": 0.0, "max": 0.0, "kwh": 0.0
    }

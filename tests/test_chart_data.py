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


def test_downsampling_buckets_by_time_not_by_index():
    """
    Real series are irregular: Tuya only records a changed value, and the
    collector leaves gaps when it is restarted. Everything the chart shows
    rests on buckets being slices of TIME, not slices of the point list --
    otherwise a dense burst gets spread across the whole width and the quiet
    stretch after it gets squeezed into one bucket.

    Here 100 of the 103 samples fall in the first quarter of the span. Time
    bucketing spends one bucket on that burst and gives the sparse tail the
    other three. Index bucketing would spend three of its four buckets inside
    the burst -- so the ts < 100 count below is what tells the two apart.
    """
    points = [[t, 10.0] for t in range(100)]   # burst: ts 0..99
    points[50] = [50, 99.0]                    # with a spike inside it
    points += [[150, 20.0], [250, 30.0], [400, 40.0]]

    result = chart_data.downsample(points, buckets=4)

    # One bucket's worth of the burst survives, not three.
    assert len([p for p in result if p[0] < 100]) == 2
    assert result == [[0, 10.0], [50, 99.0], [150, 20.0], [250, 30.0], [400, 40.0]]


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
    """A ramp from 0 W to 100 W over an hour averages 50 W, so 0.05 kWh."""
    points = [[t, 100.0 * t / 3600] for t in range(0, 3601, 60)]
    result = chart_data.summarise_power(points)
    assert abs(result["kwh"] - 0.05) < 1e-9
    assert result["min"] == 0.0
    assert result["max"] == 100.0


def test_summary_skips_a_long_gap():
    """
    A gap means the collector was down, not that the load held steady.
    One hour at 100 W is 0.1 kWh; the six-hour silence that follows must
    contribute nothing rather than inventing another 0.6.
    """
    points = [[t, 100.0] for t in range(0, 3601, 60)]
    points.append([3600 + 20000, 100.0])
    result = chart_data.summarise_power(points)
    assert abs(result["kwh"] - 0.1) < 1e-9


def test_summary_of_a_single_point_does_not_divide_by_zero():
    result = chart_data.summarise_power([[100, 42.0]])
    assert result == {"min": 42.0, "avg": 42.0, "max": 42.0, "kwh": 0.0}


def test_summary_of_nothing_is_all_zeros():
    assert chart_data.summarise_power([]) == {
        "min": 0.0, "avg": 0.0, "max": 0.0, "kwh": 0.0
    }


def test_summarise_without_integrate_has_a_null_kwh():
    """
    Integrating a temperature curve does not mean anything, so a caller that
    does not ask for integration must get None back, not a bogus number --
    and must still get real min/avg/max.
    """
    points = [[0, 10.0], [60, 20.0], [120, 30.0]]
    result = chart_data.summarise(points)
    assert result["kwh"] is None
    assert result["min"] == 10.0
    assert result["max"] == 30.0
    assert result["avg"] == 20.0


def test_summarise_without_integrate_on_empty_points_is_still_null_kwh():
    result = chart_data.summarise([])
    assert result == {"min": 0.0, "avg": 0.0, "max": 0.0, "kwh": None}


def test_summarise_with_integrate_matches_summarise_power():
    points = [[t, 100.0 * t / 3600] for t in range(0, 3601, 60)]
    assert chart_data.summarise(points, integrate=True) == chart_data.summarise_power(points)

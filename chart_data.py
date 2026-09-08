#!/usr/bin/env python3
"""
Maths for the widget's charts. Pure functions, no I/O, no network.

Kept apart from history_client.py, which is about transport: this module is
about what to do with the numbers once they arrive.
"""

# A gap longer than this means the collector was down. Integrating across it
# would invent consumption that never happened, so those intervals are skipped
# and the reported kWh is honestly a lower bound.
MAX_GAP_SECONDS = 600


def downsample(points, buckets=500):
    """
    [[ts, value], ...] reduced to roughly two points per bucket.

    Each bucket contributes its lowest and its highest sample, in the order
    they actually occurred. Taking every Nth sample instead would be simpler
    and would quietly delete spikes -- which are the interesting part.

    A series already shorter than the output size is returned unchanged.
    """
    if len(points) <= buckets * 2:
        return list(points)

    first_ts = points[0][0]
    last_ts = points[-1][0]
    span = last_ts - first_ts
    if span <= 0:
        return list(points)

    width = span / buckets
    result = []
    index = 0
    total = len(points)

    for bucket in range(buckets):
        end = first_ts + (bucket + 1) * width
        lowest = highest = None
        while index < total and points[index][0] < end:
            point = points[index]
            if lowest is None or point[1] < lowest[1]:
                lowest = point
            if highest is None or point[1] > highest[1]:
                highest = point
            index += 1

        if lowest is None:
            continue
        if lowest is highest:
            result.append(lowest)
        elif lowest[0] <= highest[0]:
            result.extend([lowest, highest])
        else:
            result.extend([highest, lowest])

    # The final sample sits exactly on the last bucket's upper bound and is
    # excluded by the strict comparison above. The right edge of a chart is
    # the value the user is actually looking at, so put it back.
    if not result or result[-1][0] != last_ts:
        result.append(points[-1])

    return result


def summarise(points, integrate=False):
    """
    {"min", "avg", "max", "kwh"} for [[ts, value], ...].

    kwh is the trapezoid integral of value over time when integrate is true.
    When it is false, kwh is None rather than a number -- integrating a
    temperature curve does not mean anything, and a silent bogus figure
    would be worse than an explicit "not applicable".
    """
    if not points:
        return {"min": 0.0, "avg": 0.0, "max": 0.0, "kwh": 0.0 if integrate else None}

    values = [point[1] for point in points]
    result = {
        "min": min(values),
        "avg": sum(values) / len(values),
        "max": max(values),
    }

    if not integrate:
        result["kwh"] = None
        return result

    joules = 0.0
    for earlier, later in zip(points, points[1:]):
        seconds = later[0] - earlier[0]
        if 0 < seconds <= MAX_GAP_SECONDS:
            joules += (earlier[1] + later[1]) / 2 * seconds

    result["kwh"] = joules / 3_600_000
    return result


def summarise_power(points):
    """
    {"min", "avg", "max", "kwh"} for [[ts, watts], ...].

    Energy is integrated from the power curve rather than read from the
    device's add_ele counter, because that counter resets at midnight and so
    cannot answer "how much over the last 24 hours". A thin wrapper around
    summarise(points, integrate=True) so existing callers keep working
    unchanged.
    """
    return summarise(points, integrate=True)

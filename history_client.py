#!/usr/bin/env python3
"""
Chart data for the widget: from the collector server if it answers, from the
local file if it does not and the metric is one the local file holds.

The fallback is what makes the server optional for smart plugs. It cannot make
it optional for sensors -- the local file has only ever held power and voltage,
so a temperature chart with no server has nothing to draw and says so instead
of pretending.
"""
import json
import urllib.parse
import urllib.request

import chart_data
from tuya_history import get_device_history

# The only metric the local emergency cache can serve.
LOCAL_METRICS = ("power",)


def local_power(device_id, hours):
    """
    Local power history in the series shape [[ts, watts], ...].

    A separate function so tests can replace it, and so the legacy
    [ts, power, voltage] triples are converted in exactly one place.
    """
    return [[row[0], row[1]] for row in get_device_history(device_id, hours)]


def fetch_series(base_url, token, device_id, hours, metric, timeout, bucket=0):
    """[[ts, value], ...] from the server, or None if the request failed.

    bucket is seconds; 0 (the default) omits the parameter entirely and gets
    the server's raw, unbucketed rows -- the same request this always sent
    before bucketing existed.
    """
    params = {"device": device_id, "metric": metric, "hours": hours}
    if bucket:
        params["bucket"] = bucket
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{base_url.rstrip('/')}/series?{query}")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception:
        return None


def fetch_summary(base_url, token, device_id, hours, metric, timeout):
    """{"min", "avg", "max", "kwh"} from the server's /summary, or None if
    the request failed."""
    query = urllib.parse.urlencode(
        {"device": device_id, "metric": metric, "hours": hours}
    )
    request = urllib.request.Request(f"{base_url.rstrip('/')}/summary?{query}")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception:
        return None


def get_series(settings_dict, token, device_id, hours, metrics, bucket=0):
    """
    (series, source, summaries).

    series maps metric name to [[ts, value], ...]. With bucket == 0 (the
    default) that is the raw series, downsampled for drawing. With bucket >
    0 the server already grouped the rows into bucket-second-wide averages
    -- an hourly series for a sensor is a few hundred points at most, not
    the tens of thousands downsample exists to shrink, and running min/max
    downsampling on top of an already-averaged series would bucket a
    bucket: the result would show peaks and troughs of the *averages*, not
    of the real readings, which is not a thing anyone asked for. So when
    bucket is truthy the rows are used exactly as the server returned them.

    source is "server", "local", "unavailable" or "empty", and is returned
    so the widget can say which one it drew rather than silently showing
    gappy local data that looks complete. summaries maps metric name to
    {"min", "avg", "max", "kwh"}, with one entry for each metric that had
    data -- a metric with nothing recorded simply has no key.

    "unavailable" and "empty" are kept apart on purpose. A request that never
    got an answer means the collector is down and the widget should say so.
    A request answered with [] means the collector is alive and simply has
    nothing recorded for this device yet -- announcing a dead server then
    would send the user debugging a machine that is fine.

    Statistics are fetched from the server's own /summary endpoint rather
    than computed here, so they are always taken from the RAW rows rather
    than from what got drawn, and fetch_summary is never given a bucket --
    it always asks for the true, ungrouped rows. That ordering is
    load-bearing for the un-bucketed case (downsampling keeps each bucket's
    minimum and maximum, so a plug at 10% duty collapses to an alternating
    0 W / 90 W series with a mean of 45 W instead of 9 W) and just as much
    for the bucketed case (an average of hourly averages is not the true
    average, and the min/max of hourly averages hides the real extremes).
    """
    base_url = settings_dict.get("history_server") or ""
    timeout = settings_dict.get("history_timeout", 3)

    # No server configured is a server we cannot reach, not a server with
    # nothing to say.
    unreachable = not base_url

    if base_url:
        series = {}
        summaries = {}
        for metric in metrics:
            rows = fetch_series(
                base_url, token, device_id, hours, metric, timeout, bucket=bucket
            )
            if rows is None:
                unreachable = True
                continue
            if rows:
                series[metric] = rows if bucket else chart_data.downsample(rows)
                summary = fetch_summary(base_url, token, device_id, hours, metric, timeout)
                if summary is not None:
                    summaries[metric] = summary
        if series:
            return series, "server", summaries

    # Either no server, or it had nothing for any requested metric.
    if any(metric in LOCAL_METRICS for metric in metrics):
        rows = local_power(device_id, hours)
        if rows:
            return (
                {"power": chart_data.downsample(rows)},
                "local",
                {"power": chart_data.summarise_power(rows)},
            )

    return {}, ("unavailable" if unreachable else "empty"), {}


def get_energy(settings_dict, token, device_ids, hours):
    """
    ({device_id: kwh_or_None, ...}, source).

    source is "server" if the collector answered for at least one device,
    "unavailable" if none of them could be reached at all (including no
    server being configured).

    Uses /summary rather than pulling the raw series: a day's worth of power
    readings is on the order of twenty thousand points, and this is called
    on a timer for every configured socket, which is exactly the shipping
    cost /summary exists to avoid. The only reason an earlier version of
    this function read raw rows instead was that /summary's own
    min/avg/max/kwh are all zero both for "nothing recorded" and for
    "recorded, and it summed to zero" -- indistinguishable from those four
    numbers alone. /summary's response carries a "points" count precisely to
    close that gap: points == 0 means nothing was recorded (report None),
    while points > 0 means the number is a real, if possibly zero, kwh
    figure (report it as-is, including 0.0). There is no local-file
    fallback here, unlike get_series: the local cache only ever holds a few
    hours, which would silently understate "today's total" for anything
    requested after sunrise.
    """
    base_url = settings_dict.get("history_server") or ""
    timeout = settings_dict.get("history_timeout", 3)

    energy = {}
    reached = False
    for device_id in device_ids:
        if not base_url:
            energy[device_id] = None
            continue
        summary = fetch_summary(base_url, token, device_id, hours, "power", timeout)
        if summary is None:
            energy[device_id] = None
            continue
        reached = True
        energy[device_id] = summary["kwh"] if summary.get("points", 0) > 0 else None

    return energy, ("server" if reached else "unavailable")

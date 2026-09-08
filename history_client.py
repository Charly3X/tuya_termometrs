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


def fetch_series(base_url, token, device_id, hours, metric, timeout):
    """[[ts, value], ...] from the server, or None if the request failed."""
    query = urllib.parse.urlencode(
        {"device": device_id, "metric": metric, "hours": hours}
    )
    request = urllib.request.Request(f"{base_url.rstrip('/')}/series?{query}")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception:
        return None


def get_series(settings_dict, token, device_id, hours, metrics):
    """
    (series, source, summary).

    series maps metric name to [[ts, value], ...], already downsampled for
    drawing. source is "server", "local" or "unavailable", and is returned so
    the widget can say which one it drew rather than silently showing gappy
    local data that looks complete. summary is the power statistics, or None
    when power was not among the metrics.

    The summary is computed from the RAW rows, before downsampling, and that
    ordering is load-bearing. Downsampling keeps each bucket's minimum and
    maximum, so a plug at 10% duty collapses to an alternating 0 W / 90 W
    series with a mean of 45 W instead of 9 W. Summarising what gets drawn
    would overstate consumption roughly fivefold.
    """
    base_url = settings_dict.get("history_server") or ""
    timeout = settings_dict.get("history_timeout", 3)

    if base_url:
        series = {}
        summary = None
        for metric in metrics:
            rows = fetch_series(base_url, token, device_id, hours, metric, timeout)
            if rows:
                if metric == "power":
                    summary = chart_data.summarise_power(rows)
                series[metric] = chart_data.downsample(rows)
        if series:
            return series, "server", summary

    # Either no server, or it had nothing for any requested metric.
    if any(metric in LOCAL_METRICS for metric in metrics):
        rows = local_power(device_id, hours)
        if rows:
            return (
                {"power": chart_data.downsample(rows)},
                "local",
                chart_data.summarise_power(rows),
            )

    return {}, "unavailable", None

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


def get_series(settings_dict, token, device_id, hours, metrics):
    """
    (series, source, summaries).

    series maps metric name to [[ts, value], ...], already downsampled for
    drawing. source is "server", "local", "unavailable" or "empty", and is
    returned so the widget can say which one it drew rather than silently
    showing gappy local data that looks complete. summaries maps metric name
    to {"min", "avg", "max", "kwh"}, with one entry for each metric that had
    data -- a metric with nothing recorded simply has no key.

    "unavailable" and "empty" are kept apart on purpose. A request that never
    got an answer means the collector is down and the widget should say so.
    A request answered with [] means the collector is alive and simply has
    nothing recorded for this device yet -- announcing a dead server then
    would send the user debugging a machine that is fine.

    Statistics are fetched from the server's own /summary endpoint rather
    than computed here, so they are always taken from the RAW rows rather
    than from what got drawn. That ordering is load-bearing: downsampling
    keeps each bucket's minimum and maximum, so a plug at 10% duty collapses
    to an alternating 0 W / 90 W series with a mean of 45 W instead of 9 W.
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
            rows = fetch_series(base_url, token, device_id, hours, metric, timeout)
            if rows is None:
                unreachable = True
                continue
            if rows:
                series[metric] = chart_data.downsample(rows)
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

    This reads each device's raw power series and integrates it directly,
    rather than going through /summary, because /summary cannot tell "no
    rows for this device" apart from "recorded and it was genuinely zero" --
    both come back as all-zero statistics. A device with nothing recorded
    must report None, not 0.0: absent data and zero consumption are
    different facts, and this is the one caller that needs to keep them
    apart. There is no local-file fallback here either, unlike get_series:
    the local cache only ever holds a few hours, which would silently
    understate "today's total" for anything requested after sunrise.
    """
    base_url = settings_dict.get("history_server") or ""
    timeout = settings_dict.get("history_timeout", 3)

    energy = {}
    reached = False
    for device_id in device_ids:
        if not base_url:
            energy[device_id] = None
            continue
        rows = fetch_series(base_url, token, device_id, hours, "power", timeout)
        if rows is None:
            energy[device_id] = None
            continue
        reached = True
        energy[device_id] = (
            chart_data.summarise(rows, integrate=True)["kwh"] if rows else None
        )

    return energy, ("server" if reached else "unavailable")

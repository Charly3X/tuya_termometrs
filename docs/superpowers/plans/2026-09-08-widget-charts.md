# Widget Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the KDE widget a chart for temperature sensors as well as smart plugs, drawn by one reusable component instead of the single hardcoded canvas it has today.

**Architecture:** Chart maths (downsampling, the power summary) moves into a small pure-Python module with its own tests. The widget's data path is unified on the API's `/series` endpoint, which lets the duplicate `/history` endpoint go. Drawing is extracted from `main.qml` into `ChartCanvas.qml`, parameterised by series, so both chart types share one implementation.

**Tech Stack:** Python 3.11 standard library, QML (Qt Quick Canvas), pytest.

Spec: [2026-09-08-widget-charts-design.md](../specs/2026-09-08-widget-charts-design.md)

## Global Constraints

- Python interpreter is always `./venv/bin/python3`. Never plain `python3`.
- No new runtime dependencies. Standard library only.
- Git commit messages in English.
- Timestamps are Unix seconds UTC everywhere.
- Secrets stay in `config.json`, behaviour in `settings.json`; both gitignored.
- The suite is 94 tests and green before this plan starts. Keep it green.
- Do NOT install QML into the plasmoid or restart plasmashell in any task except the last one.

## File Structure

| File | Responsibility |
|---|---|
| `chart_data.py` (new) | Downsampling and the power summary. Pure functions, no I/O |
| `history_client.py` (rewrite) | Fetch one or more metric series from the server; fall back |
| `tuya_client.py` (modify) | Replace the `history` CLI mode with `series` |
| `server/api.py` (modify) | Delete the `/history` route |
| `server/storage.py` (modify) | Delete `power_series`, now unused |
| `contents/ui/ChartCanvas.qml` (new) | Draw N series with left/right axes, grid, labels, hover |
| `contents/ui/main.qml` (modify) | Open the chart from either card type; socket summary row |

---

### Task 1: Chart maths

**Files:**
- Create: `chart_data.py`, `tests/test_chart_data.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `chart_data.downsample(points, buckets=500) -> list` over `[[ts, value], ...]`
  - `chart_data.summarise_power(points) -> {"min": float, "avg": float, "max": float, "kwh": float}`
  - `chart_data.MAX_GAP_SECONDS = 600`

- [ ] **Step 1: Write the failing test**

Create `tests/test_chart_data.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python3 -m pytest tests/test_chart_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chart_data'`

- [ ] **Step 3: Write the implementation**

Create `chart_data.py`:

```python
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


def summarise_power(points):
    """
    {"min", "avg", "max", "kwh"} for [[ts, watts], ...].

    Energy is integrated from the power curve rather than read from the
    device's add_ele counter, because that counter resets at midnight and so
    cannot answer "how much over the last 24 hours".
    """
    if not points:
        return {"min": 0.0, "avg": 0.0, "max": 0.0, "kwh": 0.0}

    values = [point[1] for point in points]

    joules = 0.0
    for earlier, later in zip(points, points[1:]):
        seconds = later[0] - earlier[0]
        if 0 < seconds <= MAX_GAP_SECONDS:
            joules += (earlier[1] + later[1]) / 2 * seconds

    return {
        "min": min(values),
        "avg": sum(values) / len(values),
        "max": max(values),
        "kwh": joules / 3_600_000,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python3 -m pytest tests/test_chart_data.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add chart_data.py tests/test_chart_data.py
git commit -m "Add chart downsampling and power summary"
```

---

### Task 2: Metric-generic history client

**Files:**
- Rewrite: `history_client.py`
- Rewrite: `tests/test_history_client.py`

**Interfaces:**
- Consumes: `chart_data.downsample`, `tuya_history.get_device_history`.
- Produces: `history_client.get_series(settings_dict, token, device_id, hours, metrics) -> tuple[dict, str]` where the dict maps metric name to `[[ts, value], ...]` and the string is `"server"`, `"local"` or `"unavailable"`. Also `history_client.fetch_series(...)` and `history_client.local_power(device_id, hours)`, both replaceable in tests.

- [ ] **Step 1: Write the failing test**

Replace `tests/test_history_client.py` entirely:

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import history_client

SERVER_ROWS = [[100, 23.6], [110, 23.7]]
LOCAL_ROWS = [[100, 90.9], [110, 80.0]]


@pytest.fixture
def server_url():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            body = json.dumps(SERVER_ROWS).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def local_rows(monkeypatch):
    monkeypatch.setattr(history_client, "local_power", lambda d, h: list(LOCAL_ROWS))


def test_server_answer_is_used(server_url, local_rows):
    series, source = history_client.get_series(
        {"history_server": server_url, "history_timeout": 3}, "tok", "dev", 1,
        ["temperature"],
    )
    assert series == {"temperature": SERVER_ROWS}
    assert source == "server"


def test_several_metrics_are_fetched_together(server_url, local_rows):
    series, source = history_client.get_series(
        {"history_server": server_url, "history_timeout": 3}, "tok", "dev", 1,
        ["temperature", "humidity"],
    )
    assert set(series) == {"temperature", "humidity"}
    assert source == "server"


def test_power_falls_back_to_the_local_file(local_rows):
    series, source = history_client.get_series(
        {"history_server": "http://127.0.0.1:1", "history_timeout": 1},
        "tok", "dev", 1, ["power"],
    )
    assert series == {"power": LOCAL_ROWS}
    assert source == "local"


def test_temperature_has_no_local_fallback(local_rows):
    """
    The local file only ever held power and voltage. Reporting "local" here
    would promise data that does not exist.
    """
    series, source = history_client.get_series(
        {"history_server": "http://127.0.0.1:1", "history_timeout": 1},
        "tok", "dev", 1, ["temperature", "humidity"],
    )
    assert series == {}
    assert source == "unavailable"


def test_no_server_configured_still_serves_power_locally(local_rows):
    series, source = history_client.get_series(
        {"history_server": "", "history_timeout": 3}, "tok", "dev", 1, ["power"],
    )
    assert series == {"power": LOCAL_ROWS}
    assert source == "local"


def test_empty_server_answer_is_treated_as_no_data(local_rows, monkeypatch):
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: [])
    series, source = history_client.get_series(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", "dev", 1, ["power"],
    )
    assert series == {"power": LOCAL_ROWS}
    assert source == "local"


def test_empty_everywhere_reports_unavailable(monkeypatch):
    monkeypatch.setattr(history_client, "fetch_series", lambda *a, **k: [])
    monkeypatch.setattr(history_client, "local_power", lambda d, h: [])
    series, source = history_client.get_series(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", "dev", 1, ["power"],
    )
    assert series == {}
    assert source == "unavailable"


def test_series_are_downsampled(monkeypatch):
    monkeypatch.setattr(
        history_client, "fetch_series",
        lambda *a, **k: [[i, float(i)] for i in range(12000)],
    )
    series, source = history_client.get_series(
        {"history_server": "http://example", "history_timeout": 3},
        "tok", "dev", 24, ["power"],
    )
    assert source == "server"
    assert len(series["power"]) < 12000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python3 -m pytest tests/test_history_client.py -v`
Expected: FAIL with `AttributeError: module 'history_client' has no attribute 'get_series'`

- [ ] **Step 3: Write the implementation**

Replace `history_client.py` entirely:

```python
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
    (series, source) where series maps metric name to [[ts, value], ...] and
    source is "server", "local" or "unavailable".

    The source is returned so the widget can say which one it drew, rather than
    silently showing gappy local data that looks complete.
    """
    base_url = settings_dict.get("history_server") or ""
    timeout = settings_dict.get("history_timeout", 3)

    if base_url:
        series = {}
        for metric in metrics:
            rows = fetch_series(base_url, token, device_id, hours, metric, timeout)
            if rows:
                series[metric] = chart_data.downsample(rows)
        if series:
            return series, "server"

    # Either no server, or it had nothing for any requested metric.
    local = [metric for metric in metrics if metric in LOCAL_METRICS]
    if local:
        rows = local_power(device_id, hours)
        if rows:
            return {"power": chart_data.downsample(rows)}, "local"

    return {}, "unavailable"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python3 -m pytest tests/test_history_client.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add history_client.py tests/test_history_client.py
git commit -m "Fetch chart data per metric instead of a fixed power shape"
```

---

### Task 3: The `series` CLI mode

**Files:**
- Modify: `tuya_client.py` (the `history` branch inside `__main__`)

**Interfaces:**
- Consumes: `history_client.get_series`, `chart_data.summarise_power`, `settings.load_settings`.
- Produces: the CLI contract the QML depends on —
  `tuya_client.py series <device_id> <hours> <metric[,metric]>` printing
  `{"series": {...}, "device": str, "source": str, "summary": {...} | null}`.

- [ ] **Step 1: Replace the history branch**

In `tuya_client.py`, replace the whole `if mode == "history":` block with:

```python
    # Chart mode: output one or more metric series and exit
    if mode == "series":
        device_id = args[1] if len(args) > 1 else ""
        hours = int(args[2]) if len(args) > 2 else 1
        metrics = (args[3] if len(args) > 3 else "power").split(",")
        app_settings = load_settings()
        token = (load_config() or {}).get("history_token", "")
        series, source = history_client.get_series(
            app_settings, token, device_id, hours, metrics
        )
        # The summary only means anything for a power curve.
        summary = (
            chart_data.summarise_power(series["power"])
            if "power" in series else None
        )
        print(json.dumps({
            "series": series,
            "device": device_id,
            "source": source,
            "summary": summary,
        }))
        sys.exit(0)
```

Add the import beside the existing `import history_client`:

```python
import chart_data
```

- [ ] **Step 2: Verify against the live server**

Run: `./venv/bin/python3 tuya_client.py series bf891cacc88ce07d8esvr2 6 power`

Expected: JSON with `"source": "server"`, a non-empty `series.power`, and a
`summary` whose `max` is roughly the fridge's running wattage (about 90).

Run: `./venv/bin/python3 tuya_client.py series bfac5ed45637af7f13fzwd 6 temperature,humidity`

Expected: `"source": "server"`, both metrics present, `"summary": null`.

- [ ] **Step 3: Verify the sensor fallback says unavailable**

Run:

```bash
./venv/bin/python3 -c "
import json, history_client
series, source = history_client.get_series(
    {'history_server': 'http://127.0.0.1:1', 'history_timeout': 1},
    'tok', 'bfac5ed45637af7f13fzwd', 6, ['temperature', 'humidity'])
print(source, series)
"
```

Expected: `unavailable {}`

- [ ] **Step 4: Commit**

```bash
git add tuya_client.py
git commit -m "Replace the history CLI mode with a metric-generic series mode"
```

---

### Task 4: Retire the duplicate endpoint

**Files:**
- Modify: `server/api.py`, `server/storage.py`
- Modify: `tests/test_api.py`, `tests/test_storage.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new. This task only removes.

- [ ] **Step 1: Confirm nothing still calls them**

```bash
grep -rn "power_series\|/history" --include="*.py" --include="*.qml" . | grep -v venv | grep -v "^./docs"
```

Expected: matches only in `server/api.py`, `server/storage.py` and their two
test files. If anything else appears, stop and report rather than deleting.

- [ ] **Step 2: Delete the route**

In `server/api.py`, remove the `/history` branch from `do_GET` so that only
`/series` and the 404 remain. The surrounding `hours` parsing, the clamp and
the auth check all stay exactly as they are.

- [ ] **Step 3: Delete the now-unused query**

In `server/storage.py`, delete `power_series` entirely, including its
docstring about carrying voltage forward.

- [ ] **Step 4: Drop the tests that covered them**

In `tests/test_api.py`, delete `test_history_returns_the_widget_shape` and
`test_unknown_device_is_an_empty_list_not_an_error`, and retarget every other
test that requests `/history` at `/series?...&metric=power`. Do not delete a
test that covers auth, `hours` parsing or the 501 behaviour — retarget it.

In `tests/test_storage.py`, delete the two `power_series` tests.

- [ ] **Step 5: Run the whole suite**

Run: `./venv/bin/python3 -m pytest -q`
Expected: all pass. The count drops by four from wherever Task 3 left it.

- [ ] **Step 6: Verify the live API still serves the widget**

Run:

```bash
./venv/bin/python3 tuya_client.py series bf891cacc88ce07d8esvr2 1 power
```

Expected: `"source": "server"` with data. This proves the widget's only
remaining path works against the deployed server, which still runs the old
code — `/series` existed there all along.

- [ ] **Step 7: Commit**

```bash
git add server/api.py server/storage.py tests/test_api.py tests/test_storage.py
git commit -m "Remove the /history endpoint, superseded by /series"
```

---

### Task 5: The chart component

**Files:**
- Create: `contents/ui/ChartCanvas.qml`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a QML component with these properties, which Task 6 sets —
  - `series`: list of objects `{points: [[ts, v], ...], color: string, unit: string, axis: "left" | "right", label: string}`
  - `emptyText`: string drawn centred when there is nothing to plot
  - `repaint()`: function forcing a redraw

- [ ] **Step 1: Write the component**

Create `contents/ui/ChartCanvas.qml`:

```qml
import QtQuick

Item {
    id: chart

    // Each entry: {points: [[ts, value], ...], color, unit, axis, label}
    property var series: []
    property string emptyText: "Нет данных"

    function repaint() { canvas.requestPaint() }
    onSeriesChanged: canvas.requestPaint()

    // Cursor position for the hover readout, -1 when the mouse is away
    property real cursorX: -1

    function _bounds(list) {
        var lo = Infinity, hi = -Infinity
        for (var s = 0; s < list.length; s++) {
            var pts = list[s].points
            for (var i = 0; i < pts.length; i++) {
                if (pts[i][1] < lo) lo = pts[i][1]
                if (pts[i][1] > hi) hi = pts[i][1]
            }
        }
        if (lo === Infinity) return [0, 1]
        var pad = (hi - lo) * 0.12
        if (pad < 0.5) pad = 0.5
        return [lo - pad, hi + pad]
    }

    function _timeSpan() {
        var lo = Infinity, hi = -Infinity
        for (var s = 0; s < series.length; s++) {
            var pts = series[s].points
            if (!pts.length) continue
            if (pts[0][0] < lo) lo = pts[0][0]
            if (pts[pts.length - 1][0] > hi) hi = pts[pts.length - 1][0]
        }
        if (lo === Infinity) return [0, 1]
        if (hi - lo < 1) hi = lo + 1
        return [lo, hi]
    }

    function _hasData() {
        for (var s = 0; s < series.length; s++)
            if (series[s].points && series[s].points.length > 1) return true
        return false
    }

    Canvas {
        id: canvas
        anchors.fill: parent
        readonly property int padL: 42
        readonly property int padR: 42
        readonly property int padT: 12
        readonly property int padB: 24

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            if (!chart._hasData()) {
                ctx.fillStyle = Qt.rgba(1, 1, 1, 0.3)
                ctx.font = "13px sans-serif"
                ctx.textAlign = "center"
                ctx.fillText(chart.emptyText, width / 2, height / 2)
                return
            }

            var cw = width - padL - padR
            var ch = height - padT - padB

            var left = [], right = []
            for (var s = 0; s < chart.series.length; s++)
                (chart.series[s].axis === "right" ? right : left).push(chart.series[s])

            var lb = chart._bounds(left)
            var rb = right.length ? chart._bounds(right) : [0, 1]
            var span = chart._timeSpan()
            var tRange = span[1] - span[0]

            // Grid and value labels
            ctx.strokeStyle = Qt.rgba(1, 1, 1, 0.06)
            ctx.lineWidth = 1
            ctx.font = "9px sans-serif"
            for (var g = 0; g <= 4; g++) {
                var gy = padT + ch * g / 4
                ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(padL + cw, gy); ctx.stroke()

                if (left.length) {
                    ctx.fillStyle = left[0].color
                    ctx.textAlign = "right"
                    ctx.fillText((lb[1] - (lb[1] - lb[0]) * g / 4).toFixed(0) + left[0].unit,
                                 padL - 5, gy + 3)
                }
                if (right.length) {
                    ctx.fillStyle = right[0].color
                    ctx.textAlign = "left"
                    ctx.fillText((rb[1] - (rb[1] - rb[0]) * g / 4).toFixed(0) + right[0].unit,
                                 padL + cw + 5, gy + 3)
                }
            }

            // Time labels
            ctx.fillStyle = Qt.rgba(1, 1, 1, 0.3)
            ctx.textAlign = "center"
            for (var x = 0; x <= 4; x++) {
                var d = new Date((span[0] + tRange * x / 4) * 1000)
                ctx.fillText(("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2),
                             padL + cw * x / 4, height - 5)
            }

            // Series
            for (var si = 0; si < chart.series.length; si++) {
                var ser = chart.series[si]
                var pts = ser.points
                if (!pts || pts.length < 2) continue
                var b = ser.axis === "right" ? rb : lb
                var vRange = b[1] - b[0]
                var primary = si === 0

                function px(i) { return padL + ((pts[i][0] - span[0]) / tRange) * cw }
                function py(i) { return padT + ch - ((pts[i][1] - b[0]) / vRange) * ch }

                if (primary) {
                    ctx.beginPath()
                    for (var f = 0; f < pts.length; f++) {
                        if (f === 0) ctx.moveTo(px(f), py(f)); else ctx.lineTo(px(f), py(f))
                    }
                    ctx.lineTo(padL + cw, padT + ch)
                    ctx.lineTo(padL, padT + ch)
                    ctx.closePath()
                    var grad = ctx.createLinearGradient(0, padT, 0, padT + ch)
                    grad.addColorStop(0, Qt.alpha(ser.color, 0.28))
                    grad.addColorStop(1, Qt.alpha(ser.color, 0.0))
                    ctx.fillStyle = grad
                    ctx.fill()
                }

                ctx.beginPath()
                for (var l = 0; l < pts.length; l++) {
                    if (l === 0) ctx.moveTo(px(l), py(l)); else ctx.lineTo(px(l), py(l))
                }
                ctx.strokeStyle = ser.color
                ctx.lineWidth = primary ? 2.0 : 1.5
                ctx.lineJoin = "round"
                ctx.stroke()

                ctx.beginPath()
                ctx.arc(px(pts.length - 1), py(pts.length - 1), 3, 0, Math.PI * 2)
                ctx.fillStyle = ser.color
                ctx.fill()
            }

            // Hover guide
            if (chart.cursorX >= padL && chart.cursorX <= padL + cw) {
                ctx.beginPath()
                ctx.moveTo(chart.cursorX, padT)
                ctx.lineTo(chart.cursorX, padT + ch)
                ctx.strokeStyle = Qt.rgba(1, 1, 1, 0.25)
                ctx.lineWidth = 1
                ctx.stroke()
            }
        }
    }

    // Readout following the cursor
    Row {
        id: readout
        visible: chart.cursorX >= 0
        spacing: 8
        y: 2
        x: Math.min(Math.max(chart.cursorX - width / 2, 4), chart.width - width - 4)

        Repeater {
            model: chart.series
            Text {
                property var nearest: chart._nearest(modelData.points, chart.cursorX)
                visible: nearest !== null
                text: nearest ? nearest[1].toFixed(1) + modelData.unit : ""
                color: modelData.color
                font.pixelSize: 11
                font.bold: true
            }
        }
    }

    Text {
        visible: chart.cursorX >= 0
        anchors.horizontalCenter: readout.horizontalCenter
        y: 18
        text: chart._cursorTime()
        color: Qt.rgba(1, 1, 1, 0.4)
        font.pixelSize: 9
    }

    function _cursorTime() {
        var pts = null
        for (var s = 0; s < series.length; s++)
            if (series[s].points && series[s].points.length) { pts = series[s].points; break }
        if (!pts) return ""
        var p = _nearest(pts, cursorX)
        if (!p) return ""
        var d = new Date(p[0] * 1000)
        return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2)
    }

    function _nearest(pts, x) {
        if (!pts || !pts.length || x < 0) return null
        var span = _timeSpan()
        var cw = width - canvas.padL - canvas.padR
        var frac = (x - canvas.padL) / cw
        if (frac < 0 || frac > 1) return null
        var target = span[0] + (span[1] - span[0]) * frac
        var best = pts[0], bestGap = Math.abs(pts[0][0] - target)
        for (var i = 1; i < pts.length; i++) {
            var gap = Math.abs(pts[i][0] - target)
            if (gap < bestGap) { best = pts[i]; bestGap = gap }
        }
        return best
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onPositionChanged: { chart.cursorX = mouse.x; canvas.requestPaint() }
        onExited: { chart.cursorX = -1; canvas.requestPaint() }
    }
}
```

- [ ] **Step 2: Check it parses**

Run:

```bash
./venv/bin/python3 -c "
src = open('contents/ui/ChartCanvas.qml').read()
assert src.count('{') == src.count('}'), 'unbalanced braces'
assert src.count('(') == src.count(')'), 'unbalanced parentheses'
for name in ['property var series', 'function repaint', 'MouseArea', 'hoverEnabled']:
    assert name in src, name
print('ChartCanvas.qml looks structurally sound')
"
```

If `qmllint` is installed, also run `qmllint contents/ui/ChartCanvas.qml` and
report its output. Do NOT install it if missing.

- [ ] **Step 3: Commit**

```bash
git add contents/ui/ChartCanvas.qml
git commit -m "Add a reusable chart component with two axes and a hover readout"
```

---

### Task 6: Wire both card types to the chart

**Files:**
- Modify: `contents/ui/main.qml`

**Interfaces:**
- Consumes: `ChartCanvas.qml` from Task 5; the `series` CLI contract from Task 3.
- Produces: nothing consumed later.

- [ ] **Step 1: Replace the chart state properties**

In `main.qml`, replace the block that currently declares `chartDeviceId`,
`chartDeviceName`, `chartData`, `chartPeriod`, `chartVisible` and `chartSource`
with:

```qml
    // Chart properties
    property string chartDeviceId: ""
    property string chartDeviceName: ""
    property string chartKind: "socket"     // "socket" or "sensor"
    property var chartSeries: []
    property var chartSummary: null
    property int chartPeriod: 1
    property bool chartVisible: false
    property string chartSource: "server"
```

- [ ] **Step 2: Replace the data handler**

In the `onNewData` handler, replace the `if (result.history !== undefined)`
branch with:

```qml
                    if (result.series !== undefined) {
                        if (!result.device || result.device === chartDeviceId) {
                            chartSource = result.source || "server"
                            chartSummary = result.summary || null
                            var built = []
                            if (chartKind === "socket") {
                                built.push({points: result.series.power || [],
                                            color: "#10b981", unit: "W",
                                            axis: "left", label: "Мощность"})
                            } else {
                                built.push({points: result.series.temperature || [],
                                            color: "#fbbf24", unit: "°",
                                            axis: "left", label: "Температура"})
                                built.push({points: result.series.humidity || [],
                                            color: "#38bdf8", unit: "%",
                                            axis: "right", label: "Влажность"})
                            }
                            chartSeries = built
                        }
                    }
```

- [ ] **Step 3: Replace the loader function**

Replace `loadChartData()` with:

```qml
    function loadChartData() {
        var metrics = chartKind === "socket" ? "power" : "temperature,humidity"
        var cmd = "/home/charoyan/projects/tuya/venv/bin/python3 "
                + "/home/charoyan/projects/tuya/tuya_client.py series "
                + chartDeviceId + " " + chartPeriod + " " + metrics
        cmd += " #" + Date.now()
        executable.connectSource(cmd)
    }
```

- [ ] **Step 4: Point the socket card at the new state**

In the socket `MouseArea`'s `onClicked`, set `chartKind` before loading:

```qml
                                        root.chartDeviceId = socketsData[index].id
                                        root.chartDeviceName = socketsData[index].name
                                        root.chartKind = "socket"
                                        root.chartPeriod = 1
                                        root.chartSeries = []
                                        root.chartSummary = null
                                        root.chartVisible = true
                                        root.loadChartData()
```

- [ ] **Step 5: Make the thermometer cards clickable**

Inside the thermometer `Repeater`'s delegate, at the end of the delegate's
children, add a `MouseArea` covering the card:

```qml
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    root.chartDeviceId = root.deviceIds[index] || ""
                                    root.chartDeviceName = deviceNames[index]
                                    root.chartKind = "sensor"
                                    root.chartPeriod = 1
                                    root.chartSeries = []
                                    root.chartSummary = null
                                    root.chartVisible = true
                                    root.loadChartData()
                                }
                            }
```

The thermometer cards currently have no device ids in QML — only names. Add a
property beside the other thermometer state:

```qml
    property var deviceIds: ["", "", ""]
```

and set it in the `onNewData` handler where the other thermometer properties
are assigned:

```qml
                        deviceIds = result.ids || ["", "", ""]
```

Then make the Python side supply them. In `tuya_sharing_api.py`, in
`get_sharing_temperatures`, add the ids to the returned dict:

```python
    return {
        "temperatures": temps,
        "humidity": humids,
        "names": names,
        "batteries": batteries,
        "ids": list(devices) + [""] * (3 - len(devices)),
    }
```

- [ ] **Step 6: Swap the canvas for the component**

Replace the whole `Canvas { id: chartCanvas ... }` block with:

```qml
                        ChartCanvas {
                            id: chartCanvas
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            series: root.chartSeries
                            emptyText: root.chartSource === "unavailable"
                                       ? "Сервер недоступен" : "Нет данных"
                        }
```

Every remaining `chartCanvas.requestPaint()` call becomes
`chartCanvas.repaint()`.

- [ ] **Step 7: Add the socket summary row**

Directly below the `ChartCanvas`, inside the same layout:

```qml
                        RowLayout {
                            Layout.fillWidth: true
                            visible: root.chartKind === "socket" && root.chartSummary !== null
                            spacing: 0

                            Repeater {
                                model: root.chartSummary ? [
                                    {k: "мин", v: root.chartSummary.min.toFixed(0) + " Вт"},
                                    {k: "сред", v: root.chartSummary.avg.toFixed(0) + " Вт"},
                                    {k: "макс", v: root.chartSummary.max.toFixed(0) + " Вт"},
                                    {k: "расход", v: root.chartSummary.kwh.toFixed(2) + " кВт·ч"}
                                ] : []

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    PlasmaComponents.Label {
                                        text: modelData.k
                                        font.pixelSize: 10
                                        color: Qt.rgba(1, 1, 1, 0.45)
                                    }
                                    PlasmaComponents.Label {
                                        text: modelData.v
                                        font.pixelSize: 10
                                        font.bold: true
                                        color: "#e8eaf0"
                                    }
                                }
                            }
                        }
```

- [ ] **Step 8: Check it parses**

```bash
./venv/bin/python3 -c "
src = open('contents/ui/main.qml').read()
assert src.count('{') == src.count('}'), 'unbalanced braces'
for name in ['ChartCanvas', 'chartKind', 'chartSummary', 'chartSeries', 'deviceIds']:
    assert name in src, name
assert 'chartCanvas.requestPaint' not in src, 'left a call to the removed Canvas API'
assert 'result.history' not in src, 'left a reference to the removed history shape'
print('main.qml looks structurally sound')
"
```

- [ ] **Step 9: Verify the ids reach the widget**

Run: `./venv/bin/python3 tuya_client.py thermometers local`

Expected: the JSON now carries an `"ids"` array of three device ids alongside
`temperatures`, `humidity`, `names` and `batteries`.

- [ ] **Step 10: Run the whole suite**

Run: `./venv/bin/python3 -m pytest -q`
Expected: all pass. If a test asserted the exact key set of the thermometer
dict, update it to expect `ids` too rather than removing the assertion.

- [ ] **Step 11: Commit**

```bash
git add contents/ui/main.qml tuya_sharing_api.py tests/
git commit -m "Chart both sockets and sensors through the shared component"
```

---

### Task 7: Install and verify

**Files:**
- No source changes. This task installs and looks.

- [ ] **Step 1: Run the whole suite one last time**

Run: `./venv/bin/python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 2: Install both QML files**

```bash
cp contents/ui/main.qml contents/ui/ChartCanvas.qml \
   ~/.local/share/plasma/plasmoids/org.kde.plasma.tuya/contents/ui/
diff -rq contents ~/.local/share/plasma/plasmoids/org.kde.plasma.tuya/contents
```

Expected: `diff` reports no differences.

- [ ] **Step 3: Restart the shell**

```bash
killall plasmashell && sleep 3 && nohup plasmashell >/dev/null 2>&1 &
```

Wait 15 seconds, then confirm it came back:

```bash
pgrep -a plasmashell | head -1
```

- [ ] **Step 4: Check for QML errors**

```bash
journalctl --user -n 200 --no-pager | grep -iE "tuya|ChartCanvas" | tail -10
```

Expected: no errors mentioning `tuya` or `ChartCanvas`. Warnings from other
plasmoids are not ours.

- [ ] **Step 5: Confirm the widget is live**

```bash
stat -c '%y' data.json && sleep 20 && stat -c '%y' data.json
```

Expected: the second timestamp is later than the first.

- [ ] **Step 6: Commit nothing, report instead**

There is nothing to commit. Report in your notes: whether the shell came back,
whether the journal was clean, and that a human still has to click a socket
card and a thermometer card to confirm both charts draw.

---

## Self-Review

**Spec coverage.** Reusable component → Task 5. Sensor chart with two axes and fixed-colour temperature line → Tasks 5 and 6. Socket chart with the summary row → Tasks 1, 3 and 6. Energy integrated rather than read from `add_ele` → Task 1, with the midnight-reset reasoning in the docstring. Unified `series` CLI → Task 3. `/history` removed → Task 4. Downsampling by min/max bucketing → Task 1. Hover readout → Task 5. Fallback matrix, including "unavailable" for sensors → Task 2. Install and restart confined to the last task → Task 7. Every spec section maps to a task.

**Type consistency.** `chart_data.downsample(points, buckets=500)` and `chart_data.summarise_power(points)` keep those signatures in Tasks 1, 2 and 3. `history_client.get_series(settings_dict, token, device_id, hours, metrics)` returns `(dict, str)` in its tests, its implementation and the Task 3 call site. The CLI's printed keys — `series`, `device`, `source`, `summary` — are exactly the keys Task 6's handler reads. `ChartCanvas`'s `series` entries carry `points`, `color`, `unit`, `axis` and `label` in both Task 5's implementation and Task 6's construction, and Task 6 calls `repaint()`, which Task 5 defines.

**Known gap, deliberate.** Task 6 adds an `ids` array to the thermometer payload, which only the sharing backend produces. The legacy IoT Core path in `tuya_client.get_temperatures` returns no ids, so a sensor card would have an empty id and its chart would come back empty. That path is dead on this account — the IoT Core subscription expired months ago and is what started this whole project — so wiring it is not worth the churn. If it is ever revived, the same three lines apply there.

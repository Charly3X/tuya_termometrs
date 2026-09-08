import QtQuick

Item {
    id: chart

    // Each entry: {points: [[ts, value], ...], color, unit, axis, label,
    //              floorAtZero}
    // floorAtZero is optional and marks a quantity that cannot go below zero,
    // so the padded axis is not allowed to invent negative watts.
    property var series: []
    property string emptyText: "Нет данных"

    // The period the user asked for, in epoch seconds. When set, the time
    // axis spans this instead of the first and last sample: with two samples
    // an hour apart, a 24-hour selection must not draw a full-width line and
    // label it as one hour.
    property real windowStart: 0
    property real windowEnd: 0

    function repaint() { canvas.requestPaint() }
    onSeriesChanged: canvas.requestPaint()
    onWindowStartChanged: canvas.requestPaint()
    onWindowEndChanged: canvas.requestPaint()

    // Cursor position for the hover readout, -1 when the mouse is away
    property real cursorX: -1

    // Qt6 QML has no Qt.alpha(color, a) helper, and Qt.darker's factor
    // semantics are easy to get wrong (factor is *100, so 100 means "no
    // change" is wrong too — it's actually a heavy darken). To avoid relying
    // on any Qt color-coercion helper, parse the hex string directly.
    function _withAlpha(hex, a) {
        var h = String(hex).replace("#", "")
        if (h.length === 3) {
            h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2]
        }
        if (h.length !== 6) {
            return Qt.rgba(1, 1, 1, a)   // unknown format: visible, not invisible
        }
        return Qt.rgba(parseInt(h.substr(0, 2), 16) / 255,
                       parseInt(h.substr(2, 2), 16) / 255,
                       parseInt(h.substr(4, 2), 16) / 255,
                       a)
    }

    function _bounds(list) {
        var lo = Infinity, hi = -Infinity
        var floorZero = list.length > 0
        for (var s = 0; s < list.length; s++) {
            if (!list[s].floorAtZero) floorZero = false
            var pts = list[s].points
            if (!pts) continue
            for (var i = 0; i < pts.length; i++) {
                if (pts[i][1] < lo) lo = pts[i][1]
                if (pts[i][1] > hi) hi = pts[i][1]
            }
        }
        if (lo === Infinity) return [0, 1]
        var pad = (hi - lo) * 0.12
        if (pad < 0.5) pad = 0.5
        var bottom = lo - pad
        // A plug reading 0 W would otherwise get a "-11 W" bottom label.
        // Only for quantities that declared themselves non-negative, and only
        // when the data itself stays non-negative -- temperature keeps its
        // padding below zero, because -3 °C is a real reading.
        if (floorZero && lo >= 0 && bottom < 0) bottom = 0
        return [bottom, hi + pad]
    }

    // How many decimals an axis label needs. Temperature moves less than a
    // degree in a typical window, so a whole-number axis prints
    // "26 26 26 25 25" -- five labels carrying no information.
    function _decimals(range) {
        return range < 5 ? 1 : 0
    }

    function _timeSpan() {
        var lo = Infinity, hi = -Infinity
        for (var s = 0; s < series.length; s++) {
            var pts = series[s].points
            if (!pts || !pts.length) continue
            if (pts[0][0] < lo) lo = pts[0][0]
            if (pts[pts.length - 1][0] > hi) hi = pts[pts.length - 1][0]
        }
        var haveData = lo !== Infinity

        if (windowEnd > windowStart) {
            var wlo = windowStart, whi = windowEnd
            // Widen for data outside the requested window rather than
            // clipping it: the desktop and the collector do not share a
            // clock, and a sample a few seconds "in the future" must not be
            // painted outside the plot area.
            if (haveData) {
                if (lo < wlo) wlo = lo
                if (hi > whi) whi = hi
            }
            return [wlo, whi]
        }

        if (!haveData) return [0, 1]
        if (hi - lo < 1) hi = lo + 1
        return [lo, hi]
    }

    // One point is data. Sensors record only when the value changes, roughly
    // once an hour, so a one-hour sensor window holds exactly one sample --
    // requiring two here is what made the sensor chart open on "Нет данных".
    function _hasData() {
        for (var s = 0; s < series.length; s++)
            if (series[s].points && series[s].points.length > 0) return true
        return false
    }

    // The series an axis takes its colour and unit from: the first one that
    // actually has points.
    function _axisRef(list) {
        for (var s = 0; s < list.length; s++)
            if (list[s].points && list[s].points.length) return list[s]
        return null
    }

    Canvas {
        id: canvas
        anchors.fill: parent
        // Room for a label like "102 Вт" or "26.1°C" at 9px.
        readonly property int padL: 50
        readonly property int padR: 50
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

            // An axis whose series carry no points has nothing to scale, and
            // printing its default [0, 1] beside a real curve reads as data.
            var lref = chart._axisRef(left)
            var rref = chart._axisRef(right)

            var lb = chart._bounds(left)
            var rb = right.length ? chart._bounds(right) : [0, 1]
            var lDec = chart._decimals(lb[1] - lb[0])
            var rDec = chart._decimals(rb[1] - rb[0])
            var span = chart._timeSpan()
            var tRange = span[1] - span[0]

            // Grid and value labels
            ctx.strokeStyle = Qt.rgba(1, 1, 1, 0.06)
            ctx.lineWidth = 1
            ctx.font = "9px sans-serif"
            for (var g = 0; g <= 4; g++) {
                var gy = padT + ch * g / 4
                ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(padL + cw, gy); ctx.stroke()

                if (lref) {
                    ctx.fillStyle = lref.color
                    ctx.textAlign = "right"
                    ctx.fillText((lb[1] - (lb[1] - lb[0]) * g / 4).toFixed(lDec) + lref.unit,
                                 padL - 5, gy + 3)
                }
                if (rref) {
                    ctx.fillStyle = rref.color
                    ctx.textAlign = "left"
                    ctx.fillText((rb[1] - (rb[1] - rb[0]) * g / 4).toFixed(rDec) + rref.unit,
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
                if (!pts || !pts.length) continue
                var b = ser.axis === "right" ? rb : lb
                var vRange = b[1] - b[0]
                var primary = si === 0

                // Assigned rather than declared: a function declaration inside
                // a loop body is a grey area, and these close over pts/b/span
                // that the next iteration reassigns.
                var px = function (i) {
                    return padL + ((pts[i][0] - span[0]) / tRange) * cw
                }
                var py = function (i) {
                    return padT + ch - ((pts[i][1] - b[0]) / vRange) * ch
                }

                // One sample is a dot, not a line: a zero-length path would
                // fill a triangle down to the baseline and stroke nothing.
                if (pts.length > 1) {
                    if (primary) {
                        ctx.beginPath()
                        for (var f = 0; f < pts.length; f++) {
                            if (f === 0) ctx.moveTo(px(f), py(f)); else ctx.lineTo(px(f), py(f))
                        }
                        ctx.lineTo(px(pts.length - 1), padT + ch)
                        ctx.lineTo(px(0), padT + ch)
                        ctx.closePath()
                        var grad = ctx.createLinearGradient(0, padT, 0, padT + ch)
                        grad.addColorStop(0, chart._withAlpha(ser.color, 0.28))
                        grad.addColorStop(1, chart._withAlpha(ser.color, 0.0))
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
                }

                ctx.beginPath()
                ctx.arc(px(pts.length - 1), py(pts.length - 1),
                        pts.length > 1 ? 3 : 4, 0, Math.PI * 2)
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
        x: Math.max(4, Math.min(chart.cursorX - width / 2, chart.width - width - 4))

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
        onPositionChanged: (mouse) => { chart.cursorX = mouse.x; canvas.requestPaint() }
        onExited: { chart.cursorX = -1; canvas.requestPaint() }
    }
}

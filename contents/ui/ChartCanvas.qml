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
        for (var s = 0; s < list.length; s++) {
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
        return [lo - pad, hi + pad]
    }

    function _timeSpan() {
        var lo = Infinity, hi = -Infinity
        for (var s = 0; s < series.length; s++) {
            var pts = series[s].points
            if (!pts || !pts.length) continue
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

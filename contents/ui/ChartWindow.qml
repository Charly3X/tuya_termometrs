import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents

// A standalone, resizable window for the device chart. It used to be an
// in-widget overlay (anchors.fill on a ~700x200 canvas), too small to read a
// full day of readings on. This is the same content -- header, legend,
// period buttons, canvas, summaries -- just given a window of its own so it
// can be resized to something worth looking at. No zoom, no panning, no
// re-fetching: same data, more room.
//
// Everything the window draws comes in through declared properties rather
// than reaching into main.qml's root by id, so it can be reasoned about (and
// reused) without knowing its parent's internals. Period changes go back out
// through periodRequested rather than being acted on here: main.qml keeps
// owning chartPeriod and loadChartData(), this window never builds a shell
// command.
Window {
    id: chartWindow

    // === Data, supplied by the owner (main.qml) ===
    property string deviceName: ""
    property string kind: "socket"          // "socket" or "sensor"
    property var seriesData: []
    property var summaries: ({})
    property int period: 1
    property string source: "server"        // "server" | "local" | "empty" | "unavailable"
    property real windowStart: 0
    property real windowEnd: 0

    // Emitted instead of acting locally: main.qml owns chartPeriod and
    // loadChartData(), this window only ever asks for a period.
    signal periodRequested(int hours)
    // Emitted on Escape and on the window manager's close button, so the
    // owner can flip chartVisible back to false -- without that, a second
    // click on the same card would not reopen the window.
    signal closeRequested()

    title: deviceName ? deviceName + " — график" : "График"

    width: 1000
    height: 600
    minimumWidth: 640
    minimumHeight: 420

    // Same dark panel colouring as the widget itself, so the window reads as
    // the same application. Forced fully opaque rather than the ~0.97 the
    // in-widget overlay used: a real top-level window needs
    // Qt.WA_TranslucentBackground plus compositor support to do partial
    // transparency correctly, and that is not something to gamble on under
    // an unknown Wayland setup.
    color: Qt.rgba(0.03, 0.04, 0.08, 1.0)

    // The window manager's close button lands here. Rather than letting Qt
    // close the window (which would set `visible` locally and permanently
    // sever the binding main.qml set up), refuse the close and ask the owner
    // to flip chartVisible instead -- the binding then closes the window for
    // real, and stays intact for the next open.
    onClosing: (close) => {
        close.accepted = false
        closeRequested()
    }

    Shortcut {
        sequence: "Escape"
        onActivated: closeRequested()
    }

    // Legend values: one decimal only when it carries information, so the
    // humidity reads "45%" while the power reads "91.8 Вт". Duplicated from
    // main.qml's root.formatValue rather than reached for, so this window
    // has no dependency on its parent's internals.
    function formatValue(v) {
        if (v === undefined || v === null || isNaN(v)) return "-"
        return Math.abs(v - Math.round(v)) < 0.05
               ? String(Math.round(v)) : v.toFixed(1)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        // Header
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            PlasmaComponents.Label {
                text: chartWindow.deviceName
                font.pixelSize: 16
                font.weight: Font.Bold
                color: "white"
                Layout.fillWidth: true
            }

            // Legend: one entry per drawn series, in that series' colour,
            // with its latest value. Tells the two-line sensor chart which
            // line is which.
            RowLayout {
                spacing: 10

                Repeater {
                    model: chartWindow.seriesData

                    RowLayout {
                        spacing: 4
                        // A sensor always contributes both series; the one
                        // the server has no rows for gets no legend entry.
                        visible: modelData && modelData.points
                                 && modelData.points.length > 0

                        Rectangle {
                            Layout.preferredWidth: 8
                            Layout.preferredHeight: 8
                            radius: 4
                            color: modelData ? modelData.color : "transparent"
                        }

                        PlasmaComponents.Label {
                            text: (modelData && modelData.points && modelData.points.length)
                                  ? modelData.label + " "
                                    + chartWindow.formatValue(modelData.points[modelData.points.length - 1][1])
                                    + modelData.unit
                                  : ""
                            font.pixelSize: 10
                            color: modelData ? modelData.color : "transparent"
                        }
                    }
                }
            }

            // Close button
            Rectangle {
                width: 24
                height: 24
                radius: 12
                color: Qt.rgba(1, 1, 1, 0.1)

                PlasmaComponents.Label {
                    anchors.centerIn: parent
                    text: "✕"
                    font.pixelSize: 12
                    color: Qt.rgba(1, 1, 1, 0.6)
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: chartWindow.closeRequested()
                }
            }
        }

        PlasmaComponents.Label {
            Layout.alignment: Qt.AlignHCenter
            visible: chartWindow.source === "local"
            // Just the fact, not a reason. This banner cannot tell an
            // unreachable server from a live one that simply has no rows
            // for this device yet, and claiming the former when it is the
            // latter sends someone debugging a server that is working fine.
            text: "локальные данные"
            font.pixelSize: 10
            color: "#fbbf24"
        }

        // Period selector
        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            spacing: 4

            Repeater {
                model: [{label: "1ч", hours: 1}, {label: "6ч", hours: 6}, {label: "24ч", hours: 24}]

                Rectangle {
                    width: 50
                    height: 26
                    radius: 13
                    color: chartWindow.period === modelData.hours ? "#6366f1" : Qt.rgba(1, 1, 1, 0.08)

                    PlasmaComponents.Label {
                        anchors.centerIn: parent
                        text: modelData.label
                        font.pixelSize: 11
                        font.weight: chartWindow.period === modelData.hours ? Font.Bold : Font.Normal
                        color: chartWindow.period === modelData.hours ? "white" : Qt.rgba(1, 1, 1, 0.5)
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            chartWindow.periodRequested(modelData.hours)
                            // Otherwise the amber "локальные данные" banner
                            // from the last period hangs over the empty
                            // canvas until the answer lands.
                            chartCanvas.repaint()
                        }
                    }
                }
            }
        }

        ChartCanvas {
            id: chartCanvas
            Layout.fillWidth: true
            Layout.fillHeight: true
            series: chartWindow.seriesData
            windowStart: chartWindow.windowStart
            windowEnd: chartWindow.windowEnd
            // "unavailable" is the one case where the server is actually at
            // fault; "empty" means it answered and simply has nothing for
            // this device.
            emptyText: chartWindow.source === "unavailable"
                       ? "Сервер недоступен" : "Нет данных"
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 8
            Layout.bottomMargin: 2
            visible: chartWindow.kind === "socket" && chartWindow.summaries.power !== undefined
            spacing: 10

            Repeater {
                model: chartWindow.summaries.power ? [
                    {k: "мин", v: chartWindow.summaries.power.min.toFixed(0) + " Вт", c: "#e8eaf0"},
                    {k: "сред", v: chartWindow.summaries.power.avg.toFixed(0) + " Вт", c: "#e8eaf0"},
                    {k: "макс", v: chartWindow.summaries.power.max.toFixed(0) + " Вт", c: "#e8eaf0"},
                    {k: "расход", v: chartWindow.summaries.power.kwh.toFixed(2) + " кВт·ч", c: "#e8eaf0"}
                ] : []

                // Each cell is an Item that fills its share of the row with
                // the pair centred inside it. Putting Layout.fillWidth on
                // the pair's own Row instead packs the two labels against
                // the left edge and runs every value into the next label:
                // "0 Втсред".
                Item {
                    Layout.fillWidth: true
                    implicitHeight: cell.implicitHeight

                    Row {
                        id: cell
                        anchors.centerIn: parent
                        spacing: 5

                        PlasmaComponents.Label {
                            text: modelData.k
                            font.pixelSize: 10
                            color: Qt.rgba(1, 1, 1, 0.45)
                        }
                        PlasmaComponents.Label {
                            text: modelData.v
                            font.pixelSize: 10
                            font.bold: true
                            color: modelData.c
                        }
                    }
                }
            }
        }

        // Sensor summary: min/avg/max for temperature and for humidity, one
        // row per metric that actually has data, each labelled and coloured
        // to match its series (same colours as the chart legend/axis
        // above). kwh is never shown here -- integrating a temperature or a
        // humidity curve is meaningless.
        ColumnLayout {
            Layout.fillWidth: true
            Layout.topMargin: 8
            Layout.bottomMargin: 2
            visible: chartWindow.kind === "sensor"
                     && (chartWindow.summaries.temperature !== undefined
                         || chartWindow.summaries.humidity !== undefined)
            spacing: 4

            Repeater {
                model: {
                    var groups = []
                    if (chartWindow.summaries.temperature !== undefined) {
                        var t = chartWindow.summaries.temperature
                        groups.push([
                            {k: "мин", v: t.min.toFixed(1) + "°C", c: "#fbbf24"},
                            {k: "сред", v: t.avg.toFixed(1) + "°C", c: "#fbbf24"},
                            {k: "макс", v: t.max.toFixed(1) + "°C", c: "#fbbf24"}
                        ])
                    }
                    if (chartWindow.summaries.humidity !== undefined) {
                        var h = chartWindow.summaries.humidity
                        groups.push([
                            {k: "мин", v: h.min.toFixed(0) + "%", c: "#38bdf8"},
                            {k: "сред", v: h.avg.toFixed(0) + "%", c: "#38bdf8"},
                            {k: "макс", v: h.max.toFixed(0) + "%", c: "#38bdf8"}
                        ])
                    }
                    return groups
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Repeater {
                        model: modelData

                        // Same layout approach as the socket summary row
                        // above: an Item with Layout.fillWidth and the
                        // label/value pair centred inside it, not
                        // fillWidth on the pair itself.
                        Item {
                            Layout.fillWidth: true
                            implicitHeight: sensorCell.implicitHeight

                            Row {
                                id: sensorCell
                                anchors.centerIn: parent
                                spacing: 5

                                PlasmaComponents.Label {
                                    text: modelData.k
                                    font.pixelSize: 10
                                    color: Qt.rgba(1, 1, 1, 0.45)
                                }
                                PlasmaComponents.Label {
                                    text: modelData.v
                                    font.pixelSize: 10
                                    font.bold: true
                                    color: modelData.c
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

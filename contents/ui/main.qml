import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami
import org.kde.plasma.core as PlasmaCore

PlasmoidItem {
    id: root
    
    property var temperatures: ["-", "-", "-"]
    property var humidity: ["-", "-", "-"]
    property var deviceNames: ["Loading...", "Loading...", "Loading..."]
    property var batteries: [0, 0, 0]
    property var deviceIds: ["", "", ""]
    property var socketsData: []
    // Today's consumption per socket, keyed by device id: kWh so far, or
    // null when the server has nothing recorded for that device yet. Not
    // merged into socketsData because it comes from its own slow (300s)
    // timer, separate from the 7-second socket poll.
    property var socketEnergy: ({})

    // The energy timer's triggeredOnStart fires while socketsData is still
    // empty, so its first attempt is skipped and the card would show no
    // consumption for a full 300s after every restart. Ask again the moment
    // the sockets actually arrive, but only while we still have nothing --
    // otherwise every 7-second socket poll would drag a day's integral with it.
    onSocketsDataChanged: {
        if (socketsData.length > 0 && Object.keys(socketEnergy).length === 0) {
            updateEnergy()
        }
    }
    property string thermometerUpdate: ""
    property string socketUpdate: ""
    
    // Chart properties
    property string chartDeviceId: ""
    property string chartDeviceName: ""
    property string chartKind: "socket"     // "socket" or "sensor"
    property var chartSeries: []
    property var chartSummaries: ({})
    property int chartPeriod: 1
    // Bucket width in seconds for /series, forwarded straight to
    // tuya_client.py's fourth "series" argument. 0 means unbucketed (the
    // socket chart's raw, unchanged behaviour); 3600 groups the sensor
    // chart's mostly-repeated readings into one point per hour. Set only by
    // the card click alongside chartKind -- the in-window period buttons
    // change chartPeriod but never the kind, so the bucket stays put across
    // them.
    property int chartBucket: 0
    property bool chartVisible: false
    property string chartSource: "server"
    // The window the chart's time axis spans, in epoch seconds. Derived from
    // the selected period rather than from the samples, so "24ч" looks like
    // 24 hours even when the sensor only changed value twice.
    property real chartWindowStart: 0
    property real chartWindowEnd: 0

    preferredRepresentation: fullRepresentation
    
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    
    // Temperature color based on comfort range
    function getTempColor(tempStr) {
        var temp = parseFloat(tempStr)
        if (isNaN(temp)) return "#94a3b8"
        if (temp < 18) return "#60a5fa"
        if (temp < 22) return "#34d399"
        if (temp < 26) return "#fbbf24"
        return "#f87171"
    }
    
    // Subtle card gradient based on temperature
    function getTempCardColor(tempStr) {
        var temp = parseFloat(tempStr)
        if (isNaN(temp)) return Qt.rgba(0.4, 0.4, 0.5, 0.08)
        if (temp < 18) return Qt.rgba(0.2, 0.4, 0.9, 0.08)
        if (temp < 22) return Qt.rgba(0.1, 0.7, 0.5, 0.08)
        if (temp < 26) return Qt.rgba(0.9, 0.7, 0.1, 0.06)
        return Qt.rgba(0.9, 0.3, 0.2, 0.08)
    }
    
    // Legend values: one decimal only when it carries information, so the
    // humidity reads "45%" while the power reads "91.8 Вт".
    function formatValue(v) {
        if (v === undefined || v === null || isNaN(v)) return "-"
        return Math.abs(v - Math.round(v)) < 0.05
               ? String(Math.round(v)) : v.toFixed(1)
    }

    // Today's consumption for a socket card. Three distinct states, not two:
    // no id to look up yet (nothing has arrived from the energy timer for
    // this device) and "arrived, and the server has nothing" both render as
    // an absence -- but 0.0 kWh (a real, if idle, reading) must never be
    // confused with either. Never returns "0.00" for a null/missing value.
    function formatEnergyKwh(id) {
        if (!id || !(id in socketEnergy)) return ""
        var kwh = socketEnergy[id]
        return (kwh === null || kwh === undefined) ? "—" : kwh.toFixed(2) + " кВт·ч"
    }

    function getBatteryColor(level) {
        if (level < 20) return "#ef4444"
        if (level < 40) return "#f97316"
        return "#10b981"
    }
    
    // Socket accent colors (rotating palette)
    function getSocketColors(idx) {
        var colors = [
            {bg: "#10b981", top: Qt.rgba(1,1,1,0.15), text: "#d1fae5"},
            {bg: "#f59e0b", top: Qt.rgba(1,1,1,0.12), text: "#fef3c7"}
        ]
        return colors[idx % colors.length]
    }
    
    Plasma5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        
        onNewData: (sourceName, data) => {
            if (data["exit code"] === 0) {
                try {
                    var result = JSON.parse(data.stdout)
                    var now = Qt.formatTime(new Date(), "HH:mm:ss")
                    if (result.temperatures) {
                        temperatures = result.temperatures
                        humidity = result.humidity
                        deviceNames = result.names
                        batteries = result.batteries
                        deviceIds = result.ids || ["", "", ""]
                        thermometerUpdate = now
                    }
                    if (result.socket) {
                        socketsData = [result.socket]
                        socketUpdate = now
                    }
                    if (result.sockets) {
                        socketsData = result.sockets
                        socketUpdate = now
                    }
                    if (result.energy !== undefined) {
                        // Keyed by device id, value is today's kWh so far or
                        // null when the server has no rows for that device --
                        // the two must stay visually distinct (see
                        // formatEnergyKwh), never both rendered as "0.00".
                        socketEnergy = result.energy
                    }
                    if (result.series !== undefined) {
                        if (!result.device || result.device === chartDeviceId) {
                            chartSource = result.source || "server"
                            chartSummaries = result.summaries || {}
                            var built = []
                            // Units are spelled the same here, on the axis,
                            // in the legend and in the summary row: one panel
                            // saying "W" in one place and "Вт" in another
                            // reads as two different quantities.
                            if (chartKind === "socket") {
                                built.push({points: result.series.power || [],
                                            color: "#10b981", unit: " Вт",
                                            axis: "left", label: "Мощность",
                                            floorAtZero: true})
                            } else {
                                built.push({points: result.series.temperature || [],
                                            color: "#fbbf24", unit: "°C",
                                            axis: "left", label: "Температура"})
                                built.push({points: result.series.humidity || [],
                                            color: "#38bdf8", unit: "%",
                                            axis: "right", label: "Влажность",
                                            floorAtZero: true})
                            }
                            chartSeries = built
                        }
                    }
                } catch(e) {
                    console.log("Parse error:", e)
                }
            }
            disconnectSource(sourceName)
        }
    }
    
    function updateThermometers() {
        var cmd = "/home/charoyan/projects/tuya/venv/bin/python3 /home/charoyan/projects/tuya/tuya_client.py thermometers " + plasmoid.configuration.connectionMode
        if (Plasmoid.configuration.enableLogging) {
            cmd += " --log"
        }
        cmd += " #" + Date.now()
        executable.connectSource(cmd)
    }
    
    function updateSockets() {
        var cmd = "/home/charoyan/projects/tuya/venv/bin/python3 /home/charoyan/projects/tuya/tuya_client.py socket " + plasmoid.configuration.connectionMode
        if (Plasmoid.configuration.enableLogging) {
            cmd += " --log"
        }
        cmd += " #" + Date.now()
        executable.connectSource(cmd)
    }

    function updateEnergy() {
        // socketsData is empty until the first socket poll lands, and a
        // shell command built with an empty positional loses that argument
        // to word splitting -- every argument after it then shifts left and
        // is misread by tuya_client.py (this has already cost one fix
        // round). Skip the request entirely rather than send that.
        var ids = []
        for (var i = 0; i < socketsData.length; i++) {
            if (socketsData[i] && socketsData[i].id) {
                ids.push(socketsData[i].id)
            }
        }
        if (ids.length === 0) {
            return
        }
        var cmd = "/home/charoyan/projects/tuya/venv/bin/python3 "
                + "/home/charoyan/projects/tuya/tuya_client.py energy "
                + ids.join(",")
        if (Plasmoid.configuration.enableLogging) {
            cmd += " --log"
        }
        cmd += " #" + Date.now()
        executable.connectSource(cmd)
    }

    function loadChartData() {
        // The axis shows the period that was asked for, whatever comes back.
        var nowSec = Math.floor(Date.now() / 1000)
        chartWindowEnd = nowSec
        chartWindowStart = nowSec - chartPeriod * 3600

        if (!chartDeviceId) {
            chartSeries = []
            chartSummaries = {}
            // No request went out, so nothing is known about the server:
            // "нет данных" rather than "сервер недоступен".
            chartSource = "empty"
            return
        }
        var metrics = chartKind === "socket" ? "power" : "temperature,humidity"
        var cmd = "/home/charoyan/projects/tuya/venv/bin/python3 "
                + "/home/charoyan/projects/tuya/tuya_client.py series "
                + chartDeviceId + " " + chartPeriod + " " + metrics + " " + chartBucket
        cmd += " #" + Date.now()
        executable.connectSource(cmd)
    }
    
    Timer {
        id: thermometerTimer
        interval: plasmoid.configuration.thermometerUpdateInterval * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: updateThermometers()
    }
    
    Timer {
        id: socketTimer
        interval: plasmoid.configuration.socketUpdateInterval * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: updateSockets()
    }

    // Today's consumption is a whole day's integral: it does not move
    // meaningfully minute to minute, so it gets its own slow timer rather
    // than riding along on the 7-second socket poll above.
    Timer {
        id: energyTimer
        interval: 300000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: updateEnergy()
    }

    fullRepresentation: Item {
        Layout.preferredWidth: Kirigami.Units.gridUnit * 42
        Layout.preferredHeight: Kirigami.Units.gridUnit * 15
        
        // Outer gradient border
        Rectangle {
            anchors.fill: parent
            radius: 22
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#6366f1" }
                GradientStop { position: 0.3; color: "#06b6d4" }
                GradientStop { position: 0.6; color: "#a78bfa" }
                GradientStop { position: 1.0; color: "#ec4899" }
                orientation: Gradient.Horizontal
            }
            opacity: plasmoid.configuration.backgroundOpacity
        }
        
        // Main dark panel
        Rectangle {
            id: mainPanel
            anchors.fill: parent
            anchors.margins: 3
            radius: 19
            color: Qt.rgba(0.04, 0.055, 0.1, plasmoid.configuration.backgroundOpacity)
            
            // Subtle blue-purple ambient glow top-left
            Rectangle {
                anchors.fill: parent
                radius: parent.radius
                gradient: Gradient {
                    GradientStop { position: 0.0; color: Qt.rgba(0.15, 0.12, 0.35, 0.35 * plasmoid.configuration.backgroundOpacity) }
                    GradientStop { position: 0.5; color: Qt.rgba(0.05, 0.08, 0.2, 0.15 * plasmoid.configuration.backgroundOpacity) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
            }
            
            RowLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 10
                
                // === SOCKETS (primary, left) ===
                Item {
                    Layout.preferredWidth: parent.width * 0.35
                    Layout.fillHeight: true
                    
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 6
                        
                        Repeater {
                            model: Math.min(2, socketsData.length)
                            
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                radius: 14
                                color: root.getSocketColors(index).bg
                                
                                // Glass overlay
                                Rectangle {
                                    anchors.fill: parent
                                    radius: parent.radius
                                    gradient: Gradient {
                                        GradientStop { position: 0.0; color: root.getSocketColors(index).top }
                                        GradientStop { position: 0.5; color: "transparent" }
                                        GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.15) }
                                    }
                                }
                                
                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 10
                                    spacing: 2
                                    
                                    // Device name
                                    PlasmaComponents.Label {
                                        text: socketsData[index].name
                                        font.pixelSize: 10
                                        font.weight: Font.Medium
                                        color: "white"
                                        opacity: 0.75
                                        Layout.fillWidth: true
                                    }
                                    
                                    Item { Layout.fillHeight: true }
                                    
                                    // Power value
                                    PlasmaComponents.Label {
                                        text: socketsData[index].power + " Вт"
                                        font.pixelSize: 24
                                        font.weight: Font.Bold
                                        font.letterSpacing: -0.5
                                        color: "white"
                                        Layout.fillWidth: true
                                    }
                                    
                                    // Voltage
                                    PlasmaComponents.Label {
                                        text: socketsData[index].voltage + "V"
                                        font.pixelSize: 10
                                        color: root.getSocketColors(index).text
                                        opacity: 0.6
                                        Layout.fillWidth: true
                                    }

                                    // Today's consumption, integrated server-side
                                    // from local midnight. Hidden rather than
                                    // "0.00" when nothing has arrived yet or the
                                    // server has no rows for this device -- see
                                    // formatEnergyKwh.
                                    PlasmaComponents.Label {
                                        text: "⚡ " + root.formatEnergyKwh(socketsData[index].id)
                                        font.pixelSize: 9
                                        color: root.getSocketColors(index).text
                                        opacity: 0.55
                                        Layout.fillWidth: true
                                        visible: root.formatEnergyKwh(socketsData[index].id) !== ""
                                    }
                                }

                                // Click to show chart
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        root.chartDeviceId = socketsData[index].id
                                        root.chartDeviceName = socketsData[index].name
                                        root.chartKind = "socket"
                                        // A plug reports every few seconds,
                                        // so an hour is already a full curve.
                                        root.chartPeriod = 1
                                        root.chartBucket = 0
                                        root.chartSeries = []
                                        root.chartSummaries = {}
                                        root.chartSource = "server"
                                        root.chartVisible = true
                                        root.loadChartData()
                                    }
                                }
                            }
                        }
                    }
                }

                // Separator
                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.fillHeight: true
                    Layout.topMargin: 14
                    Layout.bottomMargin: 14
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: "transparent" }
                        GradientStop { position: 0.3; color: Qt.rgba(1, 1, 1, 0.1) }
                        GradientStop { position: 0.7; color: Qt.rgba(1, 1, 1, 0.1) }
                        GradientStop { position: 1.0; color: "transparent" }
                    }
                }
                
                // === THERMOMETERS (secondary, right) ===
                ColumnLayout {
                    Layout.preferredWidth: parent.width * 0.55
                    Layout.fillHeight: true
                    spacing: 5
                    
                    Repeater {
                        model: 3
                        
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            radius: 12
                            color: root.getTempCardColor(temperatures[index])
                            border.width: 1
                            border.color: Qt.rgba(1, 1, 1, 0.08)
                            
                            // Glass highlight
                            Rectangle {
                                anchors.fill: parent
                                radius: parent.radius
                                gradient: Gradient {
                                    GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.04) }
                                    GradientStop { position: 1.0; color: "transparent" }
                                }
                            }
                            
                            Item {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                
                                // Temp icon - pinned left
                                Item {
                                    id: thermoIcon
                                    anchors.left: parent.left
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 28
                                    height: 28
                                    
                                    Rectangle {
                                        anchors.centerIn: parent
                                        width: 26
                                        height: 26
                                        radius: 13
                                        color: root.getTempColor(temperatures[index])
                                        opacity: 0.15
                                    }
                                    
                                    Kirigami.Icon {
                                        anchors.centerIn: parent
                                        source: "temperature-normal"
                                        width: 14
                                        height: 14
                                        color: root.getTempColor(temperatures[index])
                                    }
                                }
                                
                                // Device name - pinned right
                                ColumnLayout {
                                    id: thermoName
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 50
                                    spacing: 0
                                    
                                    PlasmaComponents.Label {
                                        text: deviceNames[index]
                                        font.pixelSize: 9
                                        font.weight: Font.DemiBold
                                        color: Qt.rgba(1, 1, 1, 0.5)
                                        horizontalAlignment: Text.AlignRight
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    
                                    // Battery
                                    RowLayout {
                                        Layout.alignment: Qt.AlignRight
                                        spacing: 3
                                        
                                        Rectangle {
                                            width: 24
                                            height: 3
                                            radius: 1.5
                                            color: Qt.rgba(1, 1, 1, 0.1)
                                            
                                            Rectangle {
                                                width: Math.max(2, parent.width * batteries[index] / 100)
                                                height: parent.height
                                                radius: parent.radius
                                                color: root.getBatteryColor(batteries[index])
                                            }
                                        }
                                        
                                        PlasmaComponents.Label {
                                            text: batteries[index] + "%"
                                            font.pixelSize: 7
                                            color: Qt.rgba(1, 1, 1, 0.35)
                                        }
                                    }
                                }
                                
                                // Temp + humidity - middle
                                RowLayout {
                                    anchors.left: thermoIcon.right
                                    anchors.right: thermoName.left
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.leftMargin: 6
                                    anchors.rightMargin: 6
                                    spacing: 8
                                    
                                    PlasmaComponents.Label {
                                        text: temperatures[index] + "°"
                                        font.pixelSize: 22
                                        font.weight: Font.Bold
                                        font.letterSpacing: -0.5
                                        color: "white"
                                    }
                                    
                                    RowLayout {
                                        spacing: 3
                                        
                                        Kirigami.Icon {
                                            source: "raindrop"
                                            Layout.preferredWidth: 10
                                            Layout.preferredHeight: 10
                                            color: "#38bdf8"
                                        }
                                        
                                        PlasmaComponents.Label {
                                            text: humidity[index] + "%"
                                            font.pixelSize: 11
                                            font.weight: Font.Medium
                                            color: "#7dd3fc"
                                        }
                                    }
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    root.chartDeviceId = root.deviceIds[index] || ""
                                    root.chartDeviceName = deviceNames[index]
                                    root.chartKind = "sensor"
                                    // A sensor reading is recorded only when
                                    // it changes, which is roughly hourly, so
                                    // a one-hour window holds a single sample
                                    // and opens on a lone dot. 24 hours is
                                    // the shortest period that shows a curve.
                                    root.chartPeriod = 24
                                    // A sensor writes a "still the same" row
                                    // every minute so gaps stay meaningful,
                                    // which leaves a day's worth of raw rows
                                    // carrying only a handful of distinct
                                    // values -- one point per hour is enough
                                    // to draw the same curve.
                                    root.chartBucket = 3600
                                    root.chartSeries = []
                                    root.chartSummaries = {}
                                    root.chartSource = "server"
                                    root.chartVisible = true
                                    root.loadChartData()
                                }
                            }
                        }
                    }

                    // Update timestamps
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 14
                        spacing: 4
                        
                        PlasmaComponents.Label {
                            text: socketUpdate ? "⚡ " + socketUpdate : ""
                            font.pixelSize: 8
                            Layout.fillWidth: true
                            color: Qt.rgba(1, 1, 1, 0.35)
                        }
                        
                        PlasmaComponents.Label {
                            text: thermometerUpdate ? "🌡 " + thermometerUpdate : ""
                            font.pixelSize: 8
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignRight
                            color: Qt.rgba(1, 1, 1, 0.35)
                        }
                    }
                }
            }

            // === CHART WINDOW ===
            // A separate, resizable window rather than an in-widget overlay
            // -- the widget itself is only ~700x200 in the chart area, not
            // enough to read a day of readings on. `visible` is bound one
            // way from chartVisible and never assigned to from inside
            // ChartWindow (see its onClosing), so the binding survives
            // repeated open/close cycles.
            ChartWindow {
                id: chartWindow
                visible: root.chartVisible
                deviceName: root.chartDeviceName
                kind: root.chartKind
                seriesData: root.chartSeries
                summaries: root.chartSummaries
                period: root.chartPeriod
                source: root.chartSource
                windowStart: root.chartWindowStart
                windowEnd: root.chartWindowEnd

                onPeriodRequested: (hours) => {
                    root.chartPeriod = hours
                    root.chartSeries = []
                    root.chartSummaries = {}
                    // Otherwise the amber "локальные данные" banner from
                    // the last period hangs over the empty canvas until
                    // the answer lands.
                    root.chartSource = "server"
                    root.loadChartData()
                }

                onCloseRequested: root.chartVisible = false
            }
        }
    }
}

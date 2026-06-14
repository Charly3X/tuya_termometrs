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
    property var socketsData: []
    property string thermometerUpdate: ""
    property string socketUpdate: ""
    
    // Chart properties
    property string chartDeviceId: ""
    property string chartDeviceName: ""
    property var chartData: []
    property int chartPeriod: 1
    property bool chartVisible: false
    
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
                    if (result.history !== undefined) {
                        // Only update chart if data is for the currently selected device
                        if (!result.history_device || result.history_device === chartDeviceId) {
                            chartData = result.history
                            chartCanvas.requestPaint()
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
    
    function loadChartData() {
        console.log("CHART: Loading data for device:", chartDeviceId, "period:", chartPeriod)
        var cmd = "/home/charoyan/projects/tuya/venv/bin/python3 /home/charoyan/projects/tuya/tuya_client.py history " + chartDeviceId + " " + chartPeriod
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
                                        text: socketsData[index].power + "W"
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
                                }
                                
                                // Click to show chart
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        root.chartDeviceId = socketsData[index].id
                                        root.chartDeviceName = socketsData[index].name
                                        root.chartPeriod = 1
                                        root.chartData = []
                                        chartCanvas.requestPaint()
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
                
                // === CHART OVERLAY ===
                Rectangle {
                    id: chartOverlay
                    visible: chartVisible
                    anchors.fill: parent
                    z: 100
                    color: Qt.rgba(0.03, 0.04, 0.08, 0.97)
                    radius: 19
                    
                    // Close on click outside chart
                    MouseArea {
                        anchors.fill: parent
                        onClicked: chartVisible = false
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
                                text: "⚡"
                                font.pixelSize: 18
                            }
                            
                            PlasmaComponents.Label {
                                text: chartDeviceName
                                font.pixelSize: 16
                                font.weight: Font.Bold
                                color: "white"
                                Layout.fillWidth: true
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
                                    onClicked: chartVisible = false
                                }
                            }
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
                                    color: chartPeriod === modelData.hours ? "#6366f1" : Qt.rgba(1, 1, 1, 0.08)
                                    
                                    PlasmaComponents.Label {
                                        anchors.centerIn: parent
                                        text: modelData.label
                                        font.pixelSize: 11
                                        font.weight: chartPeriod === modelData.hours ? Font.Bold : Font.Normal
                                        color: chartPeriod === modelData.hours ? "white" : Qt.rgba(1, 1, 1, 0.5)
                                    }
                                    
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            chartPeriod = modelData.hours
                                            chartData = []
                                            chartCanvas.requestPaint()
                                            loadChartData()
                                        }
                                    }
                                }
                            }
                        }
                        
                        // Chart canvas
                        Canvas {
                            id: chartCanvas
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            
                            onPaint: {
                                var ctx = getContext("2d")
                                var w = width
                                var h = height
                                ctx.clearRect(0, 0, w, h)
                                
                                if (!chartData || chartData.length < 2) {
                                    ctx.fillStyle = Qt.rgba(1, 1, 1, 0.3)
                                    ctx.font = "14px sans-serif"
                                    ctx.textAlign = "center"
                                    ctx.fillText("Нет данных", w / 2, h / 2)
                                    return
                                }
                                
                                var padL = 45, padR = 10, padT = 10, padB = 25
                                var cw = w - padL - padR
                                var ch = h - padT - padB
                                
                                // Find min/max power
                                var minP = Infinity, maxP = -Infinity
                                for (var i = 0; i < chartData.length; i++) {
                                    var p = chartData[i][1]
                                    if (p < minP) minP = p
                                    if (p > maxP) maxP = p
                                }
                                
                                // Add padding to range
                                var range = maxP - minP
                                if (range < 1) range = 1
                                minP = Math.max(0, minP - range * 0.1)
                                maxP = maxP + range * 0.1
                                range = maxP - minP
                                
                                var tMin = chartData[0][0]
                                var tMax = chartData[chartData.length - 1][0]
                                var tRange = tMax - tMin
                                if (tRange < 1) tRange = 1
                                
                                // Grid lines
                                ctx.strokeStyle = Qt.rgba(1, 1, 1, 0.06)
                                ctx.lineWidth = 1
                                var gridLines = 4
                                for (var gi = 0; gi <= gridLines; gi++) {
                                    var gy = padT + ch * gi / gridLines
                                    ctx.beginPath()
                                    ctx.moveTo(padL, gy)
                                    ctx.lineTo(padL + cw, gy)
                                    ctx.stroke()
                                    
                                    // Y-axis labels
                                    var labelVal = maxP - (range * gi / gridLines)
                                    ctx.fillStyle = Qt.rgba(1, 1, 1, 0.35)
                                    ctx.font = "10px sans-serif"
                                    ctx.textAlign = "right"
                                    ctx.fillText(labelVal.toFixed(0) + "W", padL - 5, gy + 4)
                                }
                                
                                // X-axis time labels
                                ctx.fillStyle = Qt.rgba(1, 1, 1, 0.3)
                                ctx.font = "9px sans-serif"
                                ctx.textAlign = "center"
                                var xLabels = 5
                                for (var xi = 0; xi <= xLabels; xi++) {
                                    var t = tMin + tRange * xi / xLabels
                                    var d = new Date(t * 1000)
                                    var tLabel = d.getHours().toString().padStart(2, '0') + ":" + d.getMinutes().toString().padStart(2, '0')
                                    var tx = padL + cw * xi / xLabels
                                    ctx.fillText(tLabel, tx, h - 3)
                                }
                                
                                // Draw gradient fill
                                ctx.beginPath()
                                for (var fi = 0; fi < chartData.length; fi++) {
                                    var fx = padL + ((chartData[fi][0] - tMin) / tRange) * cw
                                    var fy = padT + ch - ((chartData[fi][1] - minP) / range) * ch
                                    if (fi === 0) ctx.moveTo(fx, fy)
                                    else ctx.lineTo(fx, fy)
                                }
                                ctx.lineTo(padL + cw, padT + ch)
                                ctx.lineTo(padL, padT + ch)
                                ctx.closePath()
                                var grad = ctx.createLinearGradient(0, padT, 0, padT + ch)
                                grad.addColorStop(0, Qt.rgba(0.39, 0.4, 0.95, 0.3))
                                grad.addColorStop(1, Qt.rgba(0.39, 0.4, 0.95, 0.02))
                                ctx.fillStyle = grad
                                ctx.fill()
                                
                                // Draw line
                                ctx.beginPath()
                                for (var li = 0; li < chartData.length; li++) {
                                    var lx = padL + ((chartData[li][0] - tMin) / tRange) * cw
                                    var ly = padT + ch - ((chartData[li][1] - minP) / range) * ch
                                    if (li === 0) ctx.moveTo(lx, ly)
                                    else ctx.lineTo(lx, ly)
                                }
                                ctx.strokeStyle = "#818cf8"
                                ctx.lineWidth = 2
                                ctx.stroke()
                            }
                        }
                        
                        // Stats row
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 20
                            
                            Repeater {
                                model: {
                                    if (!chartData || chartData.length === 0) return []
                                    var min = Infinity, max = -Infinity, sum = 0
                                    for (var i = 0; i < chartData.length; i++) {
                                        var p = chartData[i][1]
                                        if (p < min) min = p
                                        if (p > max) max = p
                                        sum += p
                                    }
                                    var avg = sum / chartData.length
                                    return [
                                        {label: "Мин", value: min.toFixed(1) + "W", color: "#34d399"},
                                        {label: "Макс", value: max.toFixed(1) + "W", color: "#f87171"},
                                        {label: "Среднее", value: avg.toFixed(1) + "W", color: "#818cf8"},
                                        {label: "Точек", value: chartData.length.toString(), color: Qt.rgba(1,1,1,0.4)}
                                    ]
                                }
                                
                                RowLayout {
                                    spacing: 4
                                    PlasmaComponents.Label {
                                        text: modelData.label + ":"
                                        font.pixelSize: 9
                                        color: Qt.rgba(1, 1, 1, 0.4)
                                    }
                                    PlasmaComponents.Label {
                                        text: modelData.value
                                        font.pixelSize: 10
                                        font.weight: Font.Bold
                                        color: modelData.color
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

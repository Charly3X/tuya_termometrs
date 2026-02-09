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
    property var socketData: {"name": "", "power": "--", "voltage": "--", "energy": "--"}
    property string lastUpdate: ""
    
    preferredRepresentation: fullRepresentation
    
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    
    function getBatteryColor(level) {
        if (level < 20) return "#ef4444"
        if (level < 40) return "#f97316"
        return "#10b981"
    }
    
    function getBatteryIcon(level) {
        if (level < 40) return "battery-050"
        if (level < 80) return "battery-080"
        return "battery-100"
    }
    
    Plasma5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        
        onNewData: (sourceName, data) => {
            if (data["exit code"] === 0) {
                try {
                    var result = JSON.parse(data.stdout)
                    temperatures = result.temperatures
                    humidity = result.humidity
                    deviceNames = result.names
                    batteries = result.batteries
                    socketData = result.socket
                    lastUpdate = result.last_update || ""
                } catch(e) {
                    console.log("Parse error:", e)
                }
            }
            disconnectSource(sourceName)
        }
    }
    
    function updateData() {
        var cmd = "/home/charoyan/projects/tuya/venv/bin/python3 /home/charoyan/projects/tuya/tuya_client.py"
        executable.connectSource(cmd)
    }
    
    // Update all data every 30 seconds
    Timer {
        interval: 30000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: updateData()
    }
    
    fullRepresentation: Item {
        Layout.preferredWidth: Kirigami.Units.gridUnit * 28
        Layout.preferredHeight: Kirigami.Units.gridUnit * 12
        
        // Gradient background
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#f59e0b" }
                GradientStop { position: 0.3; color: "#78716c" }
                GradientStop { position: 0.7; color: "#ec4899" }
                GradientStop { position: 1.0; color: "#ef4444" }
                orientation: Gradient.Horizontal
            }
            radius: 24
            opacity: plasmoid.configuration.backgroundOpacity
        }
        
        // Glass panel
        Rectangle {
            anchors.fill: parent
            anchors.margins: 8
            color: Qt.rgba(0.06, 0.09, 0.16, 0.65)
            radius: 16
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 0.15)
            
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 0
                
            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0
                
                // Thermometers section
                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 0
                    
                    Repeater {
                        model: 3
                        
                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            
                            Rectangle {
                                anchors.fill: parent
                                anchors.rightMargin: index < 2 ? 1 : 0
                                color: "transparent"
                                
                                Rectangle {
                                    anchors.right: parent.right
                                    width: 1
                                    height: parent.height
                                    color: Qt.rgba(1, 1, 1, 0.1)
                                    visible: index < 2
                                }
                            
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 6
                                spacing: 6
                                
                                Item { Layout.fillHeight: true }
                                
                                Kirigami.Icon {
                                    source: "temperature-normal"
                                    Layout.preferredWidth: 22
                                    Layout.preferredHeight: 22
                                    Layout.alignment: Qt.AlignHCenter
                                    color: Qt.rgba(1, 1, 1, 0.6)
                                }
                                
                                PlasmaComponents.Label {
                                    text: temperatures[index] + "°"
                                    font.pixelSize: 32
                                    font.weight: Font.Bold
                                    horizontalAlignment: Text.AlignHCenter
                                    Layout.fillWidth: true
                                    color: "white"
                                }
                                
                                RowLayout {
                                    Layout.alignment: Qt.AlignHCenter
                                    spacing: 4
                                    
                                    Kirigami.Icon {
                                        source: "raindrop"
                                        Layout.preferredWidth: 12
                                        Layout.preferredHeight: 12
                                        color: "#7dd3fc"
                                    }
                                    
                                    PlasmaComponents.Label {
                                        text: humidity[index] + "%"
                                        font.pixelSize: 11
                                        font.weight: Font.Medium
                                        color: "#7dd3fc"
                                    }
                                }
                                
                                Item { Layout.preferredHeight: 4 }
                                
                                Rectangle {
                                    Layout.alignment: Qt.AlignHCenter
                                    Layout.preferredHeight: 20
                                    Layout.preferredWidth: childrenRect.width + 12
                                    radius: 10
                                    color: Qt.rgba(0.06, 0.73, 0.51, 0.1)
                                    
                                    RowLayout {
                                        anchors.centerIn: parent
                                        spacing: 4
                                        
                                        Kirigami.Icon {
                                            source: root.getBatteryIcon(batteries[index])
                                            Layout.preferredWidth: 10
                                            Layout.preferredHeight: 10
                                            color: root.getBatteryColor(batteries[index])
                                        }
                                        
                                        PlasmaComponents.Label {
                                            text: batteries[index] + "%"
                                            font.pixelSize: 10
                                            font.weight: Font.Medium
                                            color: root.getBatteryColor(batteries[index])
                                        }
                                    }
                                }
                                
                                PlasmaComponents.Label {
                                    text: deviceNames[index].toUpperCase()
                                    font.pixelSize: 9
                                    font.weight: Font.DemiBold
                                    horizontalAlignment: Text.AlignHCenter
                                    Layout.fillWidth: true
                                    color: Qt.rgba(1, 1, 1, 0.4)
                                }
                                
                                Item { Layout.fillHeight: true }
                            }
                            }
                        }
                    }
                }
                
                // Socket section
                Rectangle {
                    Layout.preferredWidth: 130
                    Layout.fillHeight: true
                    Layout.margins: 4
                    color: "#38bdf8"
                    radius: 14
                    
                    Rectangle {
                        anchors.fill: parent
                        gradient: Gradient {
                            GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.1) }
                            GradientStop { position: 1.0; color: "transparent" }
                        }
                        radius: parent.radius
                    }
                    
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 6
                        
                        Item { Layout.fillHeight: true }
                        
                        Rectangle {
                            Layout.preferredWidth: 40
                            Layout.preferredHeight: 40
                            Layout.alignment: Qt.AlignHCenter
                            radius: 20
                            color: Qt.rgba(1, 1, 1, 0.2)
                            border.width: 1
                            border.color: Qt.rgba(1, 1, 1, 0.1)
                            
                            PlasmaComponents.Label {
                                anchors.centerIn: parent
                                text: "⚡"
                                font.pixelSize: 24
                                color: "white"
                            }
                        }
                        
                        PlasmaComponents.Label {
                            text: socketData.power + "W"
                            font.pixelSize: 36
                            font.weight: Font.Bold
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            color: "white"
                        }
                        
                        PlasmaComponents.Label {
                            text: socketData.voltage + "V"
                            font.pixelSize: 14
                            font.weight: Font.Medium
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            color: "#bfdbfe"
                            opacity: 0.8
                        }
                        
                        PlasmaComponents.Label {
                            text: socketData.name
                            font.pixelSize: 11
                            font.weight: Font.Medium
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            color: Qt.rgba(1, 1, 1, 0.7)
                        }
                        
                        Item { Layout.fillHeight: true }
                    }
                }
            }
            }
        }
    }
}

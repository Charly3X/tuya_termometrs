import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root
    
    property var temperatures: ["-", "-", "-"]
    property var humidity: ["-", "-", "-"]
    property var deviceNames: ["Загрузка...", "Загрузка...", "Загрузка..."]
    property var batteries: [0, 0, 0]
    property var socketData: {"name": "", "power": "--", "voltage": "--", "energy": "--"}
    
    preferredRepresentation: fullRepresentation
    
    function getBatteryColor(level) {
        if (level < 10) return "#ff4444"
        if (level < 40) return "#ff9800"
        return "#4caf50"
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
        Layout.preferredWidth: Kirigami.Units.gridUnit * 32
        Layout.preferredHeight: Kirigami.Units.gridUnit * 7
        
        Rectangle {
            anchors.fill: parent
            color: Kirigami.Theme.backgroundColor
            radius: 8
            
            RowLayout {
                anchors.fill: parent
                anchors.margins: Kirigami.Units.largeSpacing
                spacing: Kirigami.Units.largeSpacing * 2
                
                // Thermometers section
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.largeSpacing
                    
                    Repeater {
                        model: 3
                        
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            color: Kirigami.Theme.alternateBackgroundColor
                            radius: 6
                            
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: Kirigami.Units.smallSpacing
                                spacing: 4
                                
                                Item { Layout.fillHeight: true }
                                
                                PlasmaComponents.Label {
                                    text: "🌡️"
                                    font.pixelSize: 20
                                    horizontalAlignment: Text.AlignHCenter
                                    Layout.fillWidth: true
                                }
                                
                                PlasmaComponents.Label {
                                    text: temperatures[index] + "°"
                                    font.pixelSize: 28
                                    font.bold: true
                                    horizontalAlignment: Text.AlignHCenter
                                    Layout.fillWidth: true
                                    color: Kirigami.Theme.highlightColor
                                }
                                
                                PlasmaComponents.Label {
                                    text: "💧 " + humidity[index] + "%"
                                    font.pixelSize: 14
                                    horizontalAlignment: Text.AlignHCenter
                                    Layout.fillWidth: true
                                    opacity: 0.9
                                }
                                
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    
                                    Item { Layout.fillWidth: true }
                                    
                                    Kirigami.Icon {
                                        source: "battery-060"
                                        Layout.preferredWidth: 16
                                        Layout.preferredHeight: 16
                                        color: root.getBatteryColor(batteries[index])
                                    }
                                    
                                    PlasmaComponents.Label {
                                        text: batteries[index] + "%"
                                        font.pixelSize: 11
                                        color: root.getBatteryColor(batteries[index])
                                        opacity: 0.8
                                    }
                                    
                                    Item { Layout.fillWidth: true }
                                }
                                
                                PlasmaComponents.Label {
                                    text: deviceNames[index]
                                    font.pixelSize: 11
                                    horizontalAlignment: Text.AlignHCenter
                                    Layout.fillWidth: true
                                    opacity: 0.6
                                }
                                
                                Item { Layout.fillHeight: true }
                            }
                        }
                    }
                }
                
                // Socket section
                Rectangle {
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 9
                    Layout.fillHeight: true
                    color: Kirigami.Theme.highlightColor
                    radius: 6
                    opacity: 0.9
                    
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: Kirigami.Units.smallSpacing
                        spacing: 4
                        
                        Item { Layout.fillHeight: true }
                        
                        PlasmaComponents.Label {
                            text: "⚡"
                            font.pixelSize: 24
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            color: "white"
                        }
                        
                        PlasmaComponents.Label {
                            text: socketData.power + "W"
                            font.pixelSize: 32
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            color: "white"
                        }
                        
                        PlasmaComponents.Label {
                            text: socketData.voltage + "V"
                            font.pixelSize: 14
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            color: "white"
                            opacity: 0.9
                        }
                        
                        Rectangle {
                            Layout.preferredHeight: 1
                            Layout.fillWidth: true
                            Layout.leftMargin: Kirigami.Units.largeSpacing
                            Layout.rightMargin: Kirigami.Units.largeSpacing
                            color: "white"
                            opacity: 0.3
                        }
                        
                        PlasmaComponents.Label {
                            text: "Сегодня: " + socketData.energy + " kWh"
                            font.pixelSize: 12
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            color: "white"
                            opacity: 0.8
                        }
                        
                        PlasmaComponents.Label {
                            text: socketData.name
                            font.pixelSize: 10
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            color: "white"
                            opacity: 0.6
                        }
                        
                        Item { Layout.fillHeight: true }
                    }
                }
            }
        }
    }
}

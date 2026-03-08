import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

Kirigami.FormLayout {
    id: generalPage
    
    property alias cfg_thermometerUpdateInterval: thermometerInterval.value
    property alias cfg_socketUpdateInterval: socketInterval.value
    property alias cfg_backgroundOpacity: opacitySlider.value
    property alias cfg_enableLogging: loggingCheckbox.checked
    property alias cfg_connectionMode: connectionModeCombo.currentValue
    
    QQC2.ComboBox {
        id: connectionModeCombo
        Kirigami.FormData.label: "Connection mode:"
        textRole: "text"
        valueRole: "value"
        model: [
            { text: "Cloud API", value: "cloud" },
            { text: "Local Network", value: "local" },
            { text: "Smart (Auto)", value: "smart" }
        ]
        Component.onCompleted: {
            currentIndex = indexOfValue(plasmoid.configuration.connectionMode)
        }
    }
    
    QQC2.SpinBox {
        id: thermometerInterval
        Kirigami.FormData.label: "Thermometer update (sec):"
        from: 30
        to: 600
        stepSize: 30
    }
    
    QQC2.SpinBox {
        id: socketInterval
        Kirigami.FormData.label: "Socket update (sec):"
        from: 3
        to: 300
        stepSize: 1
    }
    
    QQC2.Slider {
        id: opacitySlider
        Kirigami.FormData.label: "Background opacity:"
        from: 0.0
        to: 1.0
        stepSize: 0.1
    }
    
    QQC2.CheckBox {
        id: loggingCheckbox
        Kirigami.FormData.label: "Enable logging:"
    }
}

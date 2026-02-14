import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

Kirigami.FormLayout {
    id: generalPage
    
    property alias cfg_thermometerUpdateInterval: thermometerInterval.value
    property alias cfg_socketUpdateInterval: socketInterval.value
    property alias cfg_backgroundOpacity: opacitySlider.value
    
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
        from: 10
        to: 300
        stepSize: 10
    }
    
    QQC2.Slider {
        id: opacitySlider
        Kirigami.FormData.label: "Background opacity:"
        from: 0.0
        to: 1.0
        stepSize: 0.1
    }
}

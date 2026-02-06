import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    property alias cfg_backgroundOpacity: opacitySlider.value
    
    ColumnLayout {
        QQC2.Label {
            text: "Прозрачность фона:"
        }
        
        RowLayout {
            QQC2.Slider {
                id: opacitySlider
                Layout.fillWidth: true
                from: 0.0
                to: 1.0
                stepSize: 0.05
            }
            
            QQC2.Label {
                text: Math.round(opacitySlider.value * 100) + "%"
            }
        }
    }
}

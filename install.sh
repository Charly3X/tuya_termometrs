#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Create venv if not exists
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    python3 -m venv "$SCRIPT_DIR/venv"
    "$SCRIPT_DIR/venv/bin/pip" install tinytuya
fi

# Install widget
mkdir -p ~/.local/share/plasma/plasmoids/org.kde.plasma.tuya
cp metadata.json ~/.local/share/plasma/plasmoids/org.kde.plasma.tuya/
cp -r contents ~/.local/share/plasma/plasmoids/org.kde.plasma.tuya/

# Update script path in config
sed -i "s|/home/charoyan/projects/tuya|$SCRIPT_DIR|g" ~/.local/share/plasma/plasmoids/org.kde.plasma.tuya/contents/config/main.xml

# Install systemd timer
mkdir -p ~/.config/systemd/user
cp tuya-update.service ~/.config/systemd/user/
cp tuya-update.timer ~/.config/systemd/user/

# Enable and start timer
systemctl --user daemon-reload
systemctl --user enable tuya-update.timer
systemctl --user start tuya-update.timer

# Run once to create initial data
"$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/tuya_client.py"

echo "Widget installed!"
echo "Add widget: Right-click desktop → Add Widgets → Search 'Tuya Thermometers'"
echo ""
echo "To reload plasmashell: killall plasmashell && plasmashell &"

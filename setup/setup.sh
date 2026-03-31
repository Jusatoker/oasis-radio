#!/usr/bin/env bash
# Nashville Radio — One-command setup
# Run as user 'ricky' on the AcePC AK1 (IP: 192.168.2.89)
# Usage: bash setup/setup.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/opt/nashville-radio"
SERVICE_NAME="iheart-radio"
USER="${USER:-ricky}"

info()  { echo -e "\033[1;34m[setup]\033[0m $*"; }
ok()    { echo -e "\033[1;32m[  ok]\033[0m $*"; }
err()   { echo -e "\033[1;31m[err ]\033[0m $*" >&2; }

# ── 1. System packages ────────────────────────────────────────────────────────
info "Updating package list…"
sudo apt-get update -qq

info "Installing system dependencies…"
sudo apt-get install -y -qq \
    docker.io \
    docker-compose \
    python3-pip \
    python3-venv \
    libhidapi-libusb0 \
    libhidapi-hidraw0 \
    pulseaudio \
    pulseaudio-utils

ok "System packages installed."

# ── 2. Docker group ───────────────────────────────────────────────────────────
if ! groups "$USER" | grep -q docker; then
    info "Adding $USER to docker group…"
    sudo usermod -aG docker "$USER"
    ok "Added to docker group (logout/login required for group to take effect)."
fi

# ── 3. udev rules ─────────────────────────────────────────────────────────────
info "Installing Stream Deck udev rules…"
sudo cp "$REPO_DIR/setup/99-streamdeck.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
ok "udev rules installed."

# ── 4. Python Stream Deck controller deps ────────────────────────────────────
info "Installing Python packages for Stream Deck controller…"
pip3 install --user --quiet streamdeck pillow requests
ok "Python packages installed."

# ── 5. Install project to /opt ────────────────────────────────────────────────
info "Installing project to $INSTALL_DIR…"
sudo mkdir -p "$INSTALL_DIR"
sudo rsync -a --delete "$REPO_DIR/" "$INSTALL_DIR/"
sudo chown -R "$USER:$USER" "$INSTALL_DIR"
ok "Project installed to $INSTALL_DIR."

# ── 6. PulseAudio — set AudioBox USB 96 as default sink ─────────────────────
info "Configuring PulseAudio…"

# Enable PulseAudio socket for Docker access
mkdir -p ~/.config/pulse
cat > ~/.config/pulse/default.pa << 'PA'
.include /etc/pulse/default.pa
load-module module-native-protocol-unix auth-anonymous=1 socket=/run/user/1000/pulse/native
PA

# Set default sink to AudioBox USB 96 if present
AUDIOBOX_SINK=$(pactl list short sinks 2>/dev/null | grep -i "audiobox\|usb" | awk '{print $2}' | head -1 || true)
if [[ -n "$AUDIOBOX_SINK" ]]; then
    pactl set-default-sink "$AUDIOBOX_SINK" || true
    ok "AudioBox USB 96 set as default PulseAudio sink: $AUDIOBOX_SINK"
else
    echo "  (AudioBox USB 96 not detected — set default sink manually with: pactl set-default-sink <name>)"
fi

# Enable PulseAudio lingering so it starts on boot without login
info "Enabling PulseAudio user service with lingering…"
loginctl enable-linger "$USER"
systemctl --user enable pulseaudio.service pulseaudio.socket 2>/dev/null || true
systemctl --user start  pulseaudio.service 2>/dev/null || true
ok "PulseAudio configured."

# ── 7. Stream Deck systemd user service ───────────────────────────────────────
info "Installing Stream Deck controller as systemd user service…"
mkdir -p ~/.config/systemd/user
# Patch ExecStart path to use install dir
sed "s|/opt/nashville-radio|$INSTALL_DIR|g" \
    "$INSTALL_DIR/setup/iheart-radio.service" \
    > ~/.config/systemd/user/"$SERVICE_NAME".service

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME".service
systemctl --user start  "$SERVICE_NAME".service
ok "Stream Deck service installed and started."

# ── 8. Docker stack ───────────────────────────────────────────────────────────
info "Starting Docker stack…"
cd "$INSTALL_DIR"
docker-compose pull --quiet 2>/dev/null || true
docker-compose up -d --build
ok "Docker stack started."

# ── 9. Final status ───────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok "Nashville Radio is up!"
echo ""
echo "  Dashboard:    http://192.168.2.89:5000"
echo "  Stream Deck:  $(systemctl --user is-active "$SERVICE_NAME" 2>/dev/null || echo 'check: systemctl --user status iheart-radio')"
echo "  Docker:       $(docker-compose -f "$INSTALL_DIR/docker-compose.yml" ps --services 2>/dev/null | tr '\n' ' ')"
echo ""
echo "  Useful commands:"
echo "    docker-compose -f $INSTALL_DIR/docker-compose.yml logs -f"
echo "    systemctl --user status $SERVICE_NAME"
echo "    systemctl --user restart $SERVICE_NAME"
echo "    kill -HUP \$(systemctl --user show -p MainPID $SERVICE_NAME | cut -d= -f2)  # reload layout"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

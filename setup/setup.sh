#!/usr/bin/env bash
# Oasis Radio — One-command setup
# Run as user 'ricky' on the AcePC AK1
# Usage: bash setup/setup.sh
#
# Architecture:
#   AcePC AK1 = Docker host + Chromium kiosk display
#   GL-Net Mango (reflashed) = VirtualHere server
#   AudioBox USB 96 + Stream Deck+ plug into Mango, route over Ethernet

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/opt/oasis-radio"
SERVICE_NAME="oasis-radio"
USER="${USER:-ricky}"

info()  { echo -e "\033[1;34m[setup]\033[0m $*"; }
ok()    { echo -e "\033[1;32m[  ok]\033[0m $*"; }
err()   { echo -e "\033[1;31m[err ]\033[0m $*" >&2; }
ask()   { read -rp "$(echo -e "\033[1;33m[????]\033[0m $* ")" REPLY; }

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
    pulseaudio-utils \
    chromium-browser \
    xorg \
    openbox

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

# ── 4. VirtualHere client ────────────────────────────────────────────────────
VH_BIN="/usr/local/bin/vhclientx86_64"
if [[ ! -f "$VH_BIN" ]]; then
    info "Installing VirtualHere USB client…"
    curl -fsSL "https://www.virtualhere.com/sites/default/files/usbclient/vhclientx86_64" \
        -o /tmp/vhclientx86_64
    sudo install -m 755 /tmp/vhclientx86_64 "$VH_BIN"
    rm -f /tmp/vhclientx86_64
    ok "VirtualHere client installed to $VH_BIN"
else
    ok "VirtualHere client already installed."
fi

# Install VH client as systemd user service
info "Installing VirtualHere client service…"
mkdir -p ~/.config/systemd/user
cp "$REPO_DIR/setup/virtualhere-client.service" \
    ~/.config/systemd/user/virtualhere-client.service
systemctl --user daemon-reload
systemctl --user enable virtualhere-client.service
systemctl --user start virtualhere-client.service 2>/dev/null || true
ok "VirtualHere client service installed."

echo ""
info "VirtualHere auto-attach setup:"
echo "  1. Make sure the Mango is on the network with VirtualHere server running"
echo "  2. Plug AudioBox USB 96 + Stream Deck+ into the Mango's USB ports"
echo "  3. Run: vhclientx86_64 -t 'LIST'"
echo "     to see available devices"
echo "  4. Run: vhclientx86_64 -t 'AUTO USE DEVICE,<hub>.<device>'"
echo "     for each device to enable auto-attach on boot"
echo "  Devices will then auto-attach whenever VH client starts."
echo ""

# ── 5. Python Stream Deck controller deps (venv) ─────────────────────────────
info "Creating Python venv for Stream Deck controller…"
VENV_DIR="$HOME/.venv/streamdeck"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet streamdeck pillow requests
ok "Python venv created at $VENV_DIR with streamdeck, pillow, requests."

# ── 6. Install project to /opt ────────────────────────────────────────────────
info "Installing project to $INSTALL_DIR…"
sudo mkdir -p "$INSTALL_DIR"
sudo rsync -a --delete "$REPO_DIR/" "$INSTALL_DIR/"
sudo chown -R "$USER:$USER" "$INSTALL_DIR"
ok "Project installed to $INSTALL_DIR."

# ── 7. PulseAudio — configure for Docker + VirtualHere ──────────────────────
info "Configuring PulseAudio…"

mkdir -p ~/.config/pulse
cat > ~/.config/pulse/default.pa << 'PA'
.include /etc/pulse/default.pa
load-module module-native-protocol-unix auth-anonymous=1 socket=/run/user/1000/pulse/native
PA

# Enable PulseAudio lingering so it starts on boot without login
loginctl enable-linger "$USER"
systemctl --user enable pulseaudio.service pulseaudio.socket 2>/dev/null || true
systemctl --user start  pulseaudio.service 2>/dev/null || true

# Note: AudioBox will appear as a PA sink once VirtualHere attaches it.
# Set it as default after first attach:
echo ""
info "After VirtualHere attaches the AudioBox, set it as default sink:"
echo "  pactl list short sinks    # find the AudioBox sink name"
echo "  pactl set-default-sink <sink-name>"
echo ""
ok "PulseAudio configured."

# ── 8. Stream Deck systemd user service ───────────────────────────────────────
info "Installing Stream Deck controller as systemd user service…"
mkdir -p ~/.config/systemd/user
sed \
    -e "s|/opt/oasis-radio|$INSTALL_DIR|g" \
    -e "s|/usr/bin/python3|$HOME/.venv/streamdeck/bin/python3|g" \
    "$INSTALL_DIR/setup/oasis-radio.service" \
    > ~/.config/systemd/user/"$SERVICE_NAME".service

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME".service
systemctl --user start  "$SERVICE_NAME".service
ok "Stream Deck service installed and started."

# ── 9. Farm dashboard kiosk ──────────────────────────────────────────────────
ask "Notion page URL for farm dashboard (leave empty to skip):"
NOTION_URL="${REPLY:-}"

if [[ -n "$NOTION_URL" ]]; then
    info "Installing farm dashboard kiosk service…"
    sed "s|%NOTION_URL%|$NOTION_URL|g" \
        "$INSTALL_DIR/setup/farm-kiosk.service" \
        > ~/.config/systemd/user/farm-kiosk.service
    systemctl --user daemon-reload
    systemctl --user enable farm-kiosk.service
    ok "Farm kiosk service installed (starts on next login with display)."
    echo "  To start now: systemctl --user start farm-kiosk"
    echo "  To change URL: edit ~/.config/systemd/user/farm-kiosk.service"

    # Auto-login + auto-start X on tty1
    info "Configuring auto-login on tty1…"
    sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
    sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf > /dev/null << AUTOLOGIN
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $USER --noclear %I \$TERM
AUTOLOGIN

    # .bash_profile starts X if on tty1
    if ! grep -q 'startx' "$HOME/.bash_profile" 2>/dev/null; then
        cat >> "$HOME/.bash_profile" << 'XSTART'

# Auto-start X on tty1 for kiosk display
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    exec startx
fi
XSTART
    fi

    # Openbox autostart launches the kiosk
    mkdir -p ~/.config/openbox
    cat > ~/.config/openbox/autostart << 'OPENBOX'
# Disable screen blanking
xset s off
xset -dpms
xset s noblank

# Start farm kiosk
systemctl --user start farm-kiosk &
OPENBOX

    ok "Auto-login + kiosk display configured."
else
    info "Skipping farm dashboard kiosk setup."
fi

# ── 10. Docker stack ──────────────────────────────────────────────────────────
info "Starting Docker stack…"
cd "$INSTALL_DIR"
docker-compose pull --quiet 2>/dev/null || true
docker-compose up -d --build
ok "Docker stack started."

# ── 11. Final status ──────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok "Oasis Radio is up!"
echo ""
echo "  Dashboard:      http://$(hostname -I | awk '{print $1}'):5000"
echo "  VirtualHere:    $(systemctl --user is-active virtualhere-client 2>/dev/null || echo 'check: systemctl --user status virtualhere-client')"
echo "  Stream Deck:    $(systemctl --user is-active "$SERVICE_NAME" 2>/dev/null || echo 'check: systemctl --user status oasis-radio')"
echo "  Docker:         $(docker-compose -f "$INSTALL_DIR/docker-compose.yml" ps --services 2>/dev/null | tr '\n' ' ')"
if [[ -n "${NOTION_URL:-}" ]]; then
echo "  Farm Kiosk:     $(systemctl --user is-active farm-kiosk 2>/dev/null || echo 'pending next login')"
fi
echo ""
echo "  Next steps:"
echo "    1. Attach USB devices: vhclientx86_64 -t 'LIST'"
echo "    2. Auto-attach:        vhclientx86_64 -t 'AUTO USE DEVICE,<hub>.<device>'"
echo "    3. Set audio sink:     pactl set-default-sink <audiobox-sink>"
echo ""
echo "  Useful commands:"
echo "    docker-compose -f $INSTALL_DIR/docker-compose.yml logs -f"
echo "    systemctl --user status $SERVICE_NAME"
echo "    systemctl --user status virtualhere-client"
echo "    systemctl --user restart $SERVICE_NAME"
echo "    kill -HUP \$(systemctl --user show -p MainPID $SERVICE_NAME | cut -d= -f2)  # reload layout"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Oasis Radio

Self-hosted web radio dashboard with Stream Deck+ integration and mpv backend.
Dark UI · drag-and-drop stations · multi-source search · htmx-powered.

## Architecture

```
┌─────────────────────┐          Ethernet          ┌──────────────────┐
│     AcePC AK1       │◄──────────────────────────►│   GL-Net Mango   │
│                     │       VirtualHere USB/IP    │  (reflashed)     │
│  Docker (Flask+mpv) │                             │                  │
│  Stream Deck ctrl   │                             │  ┌────────────┐  │
│  Chromium kiosk     │                             │  │ AudioBox   │  │
│  PulseAudio         │                             │  │ USB 96     │  │
│                     │                             │  ├────────────┤  │
│  HDMI out ──► TV    │                             │  │ Stream     │  │
│  (Notion dashboard) │                             │  │ Deck+      │  │
└─────────────────────┘                             │  └────────────┘  │
                                                    └──────────────────┘
```

- **AcePC AK1** — Runs Docker (Oasis Radio), PulseAudio, Stream Deck controller, and a Chromium kiosk displaying a Notion farm dashboard
- **GL-Net Mango** — Reflashed with VirtualHere server; AudioBox USB 96 and Stream Deck+ plug into its USB ports and route over Ethernet to the AcePC

## Prerequisites

- AcePC AK1 (or any headless Linux box) running Debian/Ubuntu
- GL-Net Mango reflashed with VirtualHere USB server
- PreSonus AudioBox USB 96 (plugged into Mango)
- Elgato Stream Deck+ (plugged into Mango)
- Docker + docker-compose *(setup.sh handles this)*
- Monitor on HDMI *(optional, for farm dashboard kiosk)*

---

## Install (one command)

```bash
git clone https://github.com/justoker/oasis-radio
cd oasis-radio
bash setup/setup.sh
```

The setup script will:
1. Install system packages (Docker, PulseAudio, Chromium, Openbox)
2. Download and install VirtualHere USB client
3. Configure PulseAudio for Docker audio passthrough
4. Set up the Stream Deck controller with retry logic for VirtualHere
5. Optionally configure a Chromium kiosk for a Notion farm dashboard
6. Start the Docker stack

### Post-install: attach USB devices

```bash
# List devices available on the Mango
vhclientx86_64 -t 'LIST'

# Auto-attach each device (persists across reboots)
vhclientx86_64 -t 'AUTO USE DEVICE,<hub>.<device>'

# Set AudioBox as default PulseAudio sink
pactl list short sinks
pactl set-default-sink <audiobox-sink-name>
```

Open the dashboard at **http://\<acepc-ip\>:5000**

---

## Adding Stations via the Web UI

1. Open the dashboard → scroll to **Find Stations**
2. Type a station name and optional city/state, click **Search**
3. Click **+ Add** on any result — it saves to `stations.json` immediately
4. Drag cards to reorder

---

## Stream Deck Mapper

1. Click **Deck** in the header (or go to `/mapper.html`)
2. Drag stations from the left panel onto the 8 LCD key slots
3. Drag controls (Volume, Stop, Page Flip) onto the 4 dial slots
4. Click **Save Layout**

---

## Service Management

```bash
# Docker (Flask + mpv)
docker-compose logs -f
docker-compose restart

# Stream Deck controller
systemctl --user status  oasis-radio
systemctl --user restart oasis-radio

# VirtualHere client
systemctl --user status  virtualhere-client
systemctl --user restart virtualhere-client

# Farm dashboard kiosk
systemctl --user status  farm-kiosk
systemctl --user restart farm-kiosk

# Reload Stream Deck layout without restart
kill -HUP $(systemctl --user show -p MainPID oasis-radio | cut -d= -f2)
```

---

## Project Structure

```
oasis-radio/
├── docker-compose.yml              # Flask container + PulseAudio
├── Dockerfile                      # Python + mpv + PulseAudio
├── app/
│   ├── server.py                   # Flask API + mpv IPC + htmx partials
│   ├── requirements.txt            # Python dependencies
│   ├── stations.json               # Station list (editable)
│   ├── cities.json                 # City tabs (editable)
│   ├── streamdeck_layout.json      # Deck layout (editable)
│   └── static/
│       ├── index.html              # Main dashboard (htmx + vanilla JS)
│       └── mapper.html             # Stream Deck mapper
├── streamdeck/
│   └── controller.py               # Stream Deck+ controller (VH retry)
└── setup/
    ├── setup.sh                    # One-command installer
    ├── 99-streamdeck.rules         # udev rules for HID access
    ├── oasis-radio.service         # Stream Deck systemd service
    ├── virtualhere-client.service  # VirtualHere USB client service
    └── farm-kiosk.service          # Chromium kiosk for Notion
```

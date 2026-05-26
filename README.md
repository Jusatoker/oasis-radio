# Oasis Radio

Self-hosted web radio dashboard for the AcePC AK1 mini PC.
Dark UI · drag-and-drop stations · Stream Deck+ integration · mpv backend.

## Prerequisites

- AcePC AK1 (or any headless Linux box) running Debian/Ubuntu
- PreSonus AudioBox USB 96 (or any ALSA/PulseAudio sink)
- Elgato Stream Deck+ connected via USB *(optional)*
- Docker + docker-compose installed *(setup.sh handles this)*

---

## Install (one command)

```bash
git clone https://github.com/justoker/oasis-radio
cd oasis-radio
bash setup/setup.sh
```

Open the dashboard at **http://<your-ip>:5000**

---

## Adding Stations via the Web UI

1. Open the dashboard → scroll to **Find Stations**
2. Type a station name and optional city/state, click **Search**
3. Click **+ Add** on any result — it saves to `stations.json` immediately
4. Drag cards to reorder

To add a station manually, edit `app/stations.json`:

```json
{
  "id": "unique-slug",
  "name": "WXYZ 99.9",
  "slogan": "Your slogan",
  "genre": "Country",
  "city": "Nashville",
  "url": "http://stream.example.com/stream",
  "color": "#e8420a"
}
```

Then `docker-compose restart` (not required — the API serves the file live).

---

## Adding Cities

Click the **+** tab next to the city tabs, type the city name, press **Add**.
Cities are saved to `app/cities.json`.

---

## Stream Deck Mapper

1. Click **Deck** in the header (or go to `/mapper.html`)
2. Drag stations from the left panel onto the 8 LCD key slots
3. Drag controls (Volume, Stop, Page Flip) onto the 4 dial slots
4. Click **Save Layout** — the controller reloads automatically on next restart

To reload the layout without restarting the service:

```bash
kill -HUP $(systemctl --user show -p MainPID oasis-radio | cut -d= -f2)
```

---

## Service Management

```bash
# Docker (Flask + mpv)
docker-compose logs -f
docker-compose restart
docker-compose down && docker-compose up -d

# Stream Deck controller
systemctl --user status  oasis-radio
systemctl --user restart oasis-radio
systemctl --user stop    oasis-radio
journalctl --user -u oasis-radio -f
```

---

## Project Structure

```
oasis-radio/
├── docker-compose.yml          # Flask container definition
├── Dockerfile                  # Python + mpv image
├── app/
│   ├── server.py               # Flask API + mpv IPC
│   ├── requirements.txt        # Python dependencies
│   ├── stations.json           # Station list (editable)
│   ├── cities.json             # City tabs (editable)
│   ├── streamdeck_layout.json  # Deck layout (editable)
│   └── static/
│       ├── index.html          # Main dashboard
│       └── mapper.html         # Stream Deck mapper
├── streamdeck/
│   └── controller.py           # Stream Deck+ controller (runs outside Docker)
└── setup/
    ├── setup.sh                # One-command installer
    ├── 99-streamdeck.rules     # udev rules for HID access
    └── oasis-radio.service     # systemd user service unit
```

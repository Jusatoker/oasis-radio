#!/usr/bin/env python3
"""
Nashville Radio — Stream Deck+ Controller
Runs as a systemd user service outside Docker.
Reads streamdeck_layout.json at startup and on SIGHUP.
"""

import json
import os
import signal
import sys
import threading
import time
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

try:
    from StreamDeck.DeviceManager import DeviceManager
    from StreamDeck.Devices.StreamDeck import DialEventType, TouchscreenEventType
except ImportError:
    print("ERROR: streamdeck library not found. Run: pip3 install streamdeck", file=sys.stderr)
    sys.exit(1)

# ── Config ──────────────────────────────────────────────────────────────────
API_BASE = "http://127.0.0.1:5000/api"
LAYOUT_FILE = Path(__file__).parent.parent / "app" / "streamdeck_layout.json"
POLL_INTERVAL = 3  # seconds
KEY_SIZE = (72, 72)        # Stream Deck+ LCD key dimensions
TOUCH_W, TOUCH_H = (800, 100)

# Fonts — fall back to default if Bebas/DM Sans not available
def _load_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()

FONT_LARGE  = _load_font(13, bold=True)
FONT_SMALL  = _load_font(10, bold=False)
FONT_TINY   = _load_font(9, bold=False)

# Colors
C_BG       = (10, 10, 10)
C_ORANGE   = (232, 66, 10)
C_WHITE    = (240, 237, 232)
C_MUTED    = (100, 100, 100)
C_PLAYING  = (40, 15, 5)

# ── State ────────────────────────────────────────────────────────────────────
_layout       = {"keys": {}, "dials": {}}
_stations     = {}        # id → station dict
_current_id   = None
_volume       = 80
_muted        = False
_page         = 0         # for multi-page key layout
_layout_lock  = threading.Lock()
_deck         = None


# ── API helpers ──────────────────────────────────────────────────────────────

def _api(method: str, path: str, body=None, timeout=4):
    try:
        url = API_BASE + path
        if method == "GET":
            r = requests.get(url, timeout=timeout)
        else:
            r = requests.post(url, json=body, timeout=timeout)
        return r.json()
    except Exception as exc:
        print(f"[api] {method} {path} failed: {exc}", file=sys.stderr)
        return None


# ── Layout loading ────────────────────────────────────────────────────────────

def _load_layout():
    global _layout, _stations
    try:
        with open(LAYOUT_FILE, encoding="utf-8") as f:
            _layout = json.load(f)
    except Exception as exc:
        print(f"[layout] load failed: {exc}", file=sys.stderr)
        _layout = {"keys": {}, "dials": {}}

    # Fetch station data for fast lookup
    data = _api("GET", "/stations") or []
    _stations = {s["id"]: s for s in data}
    print(f"[layout] loaded {len(_stations)} stations")


def _sighup_handler(signum, frame):
    print("[layout] SIGHUP received — reloading layout")
    with _layout_lock:
        _load_layout()
    if _deck:
        _render_all()


# ── Image rendering ───────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str):
    hex_color = hex_color.lstrip("#")
    try:
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return C_MUTED


def _render_station_key(station: dict, playing: bool) -> bytes:
    img = Image.new("RGB", KEY_SIZE, C_PLAYING if playing else C_BG)
    draw = ImageDraw.Draw(img)

    # Color accent bar (left edge)
    color = _hex_to_rgb(station.get("color", "#555555"))
    draw.rectangle([0, 0, 3, KEY_SIZE[1]], fill=color)

    # Orange glow border when playing
    if playing:
        draw.rectangle([0, 0, KEY_SIZE[0]-1, KEY_SIZE[1]-1],
                       outline=C_ORANGE, width=2)

    # Station name (split if long)
    name = station.get("name", "")
    # Try to fit in two lines
    words = name.split()
    lines = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), test, font=FONT_LARGE)
        if bbox[2] - bbox[0] > KEY_SIZE[0] - 10 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)

    y = 18
    for line in lines[:2]:
        draw.text((KEY_SIZE[0]//2, y), line, font=FONT_LARGE,
                  fill=C_ORANGE if playing else C_WHITE, anchor="mm")
        y += 16

    # Genre
    genre = station.get("genre", "")[:10]
    draw.text((KEY_SIZE[0]//2, KEY_SIZE[1] - 10), genre,
              font=FONT_TINY, fill=C_MUTED, anchor="mm")

    # EQ bars when playing
    if playing:
        import random
        bx = KEY_SIZE[0] - 14
        for j in range(4):
            h = random.randint(4, 14)
            draw.rectangle([bx + j*3, KEY_SIZE[1]//2 - h,
                            bx + j*3 + 2, KEY_SIZE[1]//2], fill=C_ORANGE)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _render_empty_key(index: int) -> bytes:
    img = Image.new("RGB", KEY_SIZE, C_BG)
    draw = ImageDraw.Draw(img)
    draw.text((KEY_SIZE[0]//2, KEY_SIZE[1]//2), str(index + 1),
              font=FONT_SMALL, fill=(40, 40, 40), anchor="mm")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def _render_key(key_index: int) -> bytes:
    slot = _layout.get("keys", {}).get(str(key_index))
    if slot and slot.get("type") == "station":
        station = _stations.get(slot["id"])
        if station:
            playing = (slot["id"] == _current_id)
            return _render_station_key(station, playing)
    return _render_empty_key(key_index)


def _render_touch_strip():
    if not hasattr(_deck, "set_touchscreen_image"):
        return
    img = Image.new("RGB", (TOUCH_W, TOUCH_H), (8, 8, 8))
    draw = ImageDraw.Draw(img)

    if _current_id and _current_id in _stations:
        s = _stations[_current_id]
        name = s.get("name", "")
        slogan = s.get("slogan", s.get("genre", ""))

        draw.text((20, TOUCH_H//2 - 10), name,
                  font=_load_font(18, bold=True), fill=C_ORANGE, anchor="lm")
        draw.text((20, TOUCH_H//2 + 12), slogan,
                  font=FONT_SMALL, fill=C_MUTED, anchor="lm")
    else:
        draw.text((20, TOUCH_H//2), "Nothing playing",
                  font=FONT_SMALL, fill=(50, 50, 50), anchor="lm")

    # Volume bar
    bar_x = TOUCH_W - 180
    bar_w = 150
    bar_y = TOUCH_H//2
    draw.rectangle([bar_x, bar_y - 2, bar_x + bar_w, bar_y + 2],
                   fill=(30, 30, 30))
    fill_w = int(bar_w * (_volume / 100))
    if fill_w > 0:
        draw.rectangle([bar_x, bar_y - 2, bar_x + fill_w, bar_y + 2],
                       fill=C_ORANGE)
    vol_label = f"{_volume}%"
    draw.text((bar_x + bar_w + 8, bar_y), vol_label,
              font=FONT_TINY, fill=C_MUTED, anchor="lm")

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    try:
        _deck.set_touchscreen_image(Image.open(BytesIO(buf.getvalue())),
                                    0, 0, TOUCH_W, TOUCH_H)
    except Exception as exc:
        print(f"[touch] render failed: {exc}", file=sys.stderr)


def _render_all():
    if not _deck:
        return
    with _deck:
        for i in range(8):
            try:
                img_bytes = _render_key(i)
                img = Image.open(BytesIO(img_bytes))
                _deck.set_key_image(i, img)
            except Exception as exc:
                print(f"[render] key {i} failed: {exc}", file=sys.stderr)
    _render_touch_strip()


# ── Event handlers ────────────────────────────────────────────────────────────

def _on_key(deck, key, state):
    if not state:  # only on press
        return
    slot = _layout.get("keys", {}).get(str(key))
    if not slot:
        return
    if slot.get("type") == "station":
        sid = slot["id"]
        global _current_id
        if _current_id == sid:
            _api("POST", "/stop")
            _current_id = None
        else:
            res = _api("POST", "/play", {"id": sid})
            if res and res.get("ok"):
                _current_id = sid
        _render_all()


def _on_dial(deck, dial, event, value):
    global _volume, _muted, _page
    slot = _layout.get("dials", {}).get(str(dial), {})
    dial_type = slot.get("type", "empty")

    if event == DialEventType.TURN:
        if dial_type == "volume":
            delta = value * 3
            new_vol = max(0, min(100, _volume + delta))
            _api("POST", "/volume", {"volume": new_vol})
            _volume = new_vol
            _render_touch_strip()
        elif dial_type == "page":
            _page = (_page + (1 if value > 0 else -1)) % 2
            _render_all()

    elif event == DialEventType.PUSH:
        if dial_type == "volume":
            # Mute toggle
            _muted = not _muted
            _api("POST", "/volume", {"volume": 0 if _muted else _volume})
        elif dial_type == "stop":
            _api("POST", "/stop")
            _current_id = None
            _render_all()
        elif dial_type == "page":
            _page = 0
            _render_all()


def _on_touch(deck, evt, value):
    pass  # touch strip is display-only


# ── Status polling ────────────────────────────────────────────────────────────

def _poll_status():
    global _current_id, _volume
    needs_render = False

    res = _api("GET", "/status")
    if res:
        new_id = res.get("station", {}).get("id") if res.get("station") else None
        new_vol = res.get("volume", _volume)

        if new_id != _current_id:
            _current_id = new_id
            needs_render = True
        if new_vol != _volume:
            _volume = new_vol
            needs_render = True

    if needs_render:
        _render_all()
    else:
        _render_touch_strip()


def _poll_loop():
    while True:
        time.sleep(POLL_INTERVAL)
        try:
            _poll_status()
        except Exception as exc:
            print(f"[poll] error: {exc}", file=sys.stderr)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _deck

    signal.signal(signal.SIGHUP, _sighup_handler)

    print("[startup] Loading layout…")
    _load_layout()

    print("[startup] Looking for Stream Deck+…")
    devices = DeviceManager().enumerate()
    if not devices:
        print("ERROR: No Stream Deck found. Check USB connection and udev rules.", file=sys.stderr)
        sys.exit(1)

    _deck = devices[0]
    _deck.open()
    _deck.reset()
    _deck.set_brightness(80)

    print(f"[startup] Connected: {_deck.deck_type()} "
          f"({_deck.key_count()} keys)")

    _deck.set_key_callback(_on_key)

    if hasattr(_deck, "set_dial_callback"):
        _deck.set_dial_callback(_on_dial)

    if hasattr(_deck, "set_touchscreen_callback"):
        _deck.set_touchscreen_callback(_on_touch)

    # Initial render
    _render_all()

    # Start polling thread
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()

    print("[startup] Running. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        _deck.reset()
        _deck.close()
        print("[shutdown] Closed Stream Deck.")


if __name__ == "__main__":
    main()

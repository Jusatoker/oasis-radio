import os
import json
import subprocess
import socket
import time
import threading
import signal

import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder='static')

STATIONS_FILE = os.environ.get('STATIONS_FILE', '/data/stations.json')
CITIES_FILE = os.environ.get('CITIES_FILE', '/data/cities.json')
LAYOUT_FILE = os.environ.get('LAYOUT_FILE', '/data/streamdeck_layout.json')
MPV_SOCKET = '/tmp/mpv-socket'

_current_station = None
_current_volume = 80
_mpv_process = None
_mpv_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def _send_mpv_command(cmd: dict) -> bool:
    """Send a JSON command to the mpv IPC socket."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(MPV_SOCKET)
        sock.sendall((json.dumps(cmd) + '\n').encode())
        time.sleep(0.05)
        sock.close()
        return True
    except Exception:
        return False


def _stop_mpv():
    global _mpv_process
    if _mpv_process is not None:
        try:
            _mpv_process.terminate()
            _mpv_process.wait(timeout=3)
        except Exception:
            try:
                _mpv_process.kill()
            except Exception:
                pass
        _mpv_process = None
    try:
        os.unlink(MPV_SOCKET)
    except FileNotFoundError:
        pass


def _start_mpv(url: str):
    global _mpv_process
    _stop_mpv()

    env = os.environ.copy()
    cmd = [
        'mpv',
        f'--input-ipc-server={MPV_SOCKET}',
        f'--volume={_current_volume}',
        '--no-video',
        '--really-quiet',
        '--cache=yes',
        '--cache-secs=5',
        url,
    ]
    _mpv_process = subprocess.Popen(cmd, env=env,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# API – Stations
# ---------------------------------------------------------------------------

@app.route('/api/stations', methods=['GET'])
def api_get_stations():
    return jsonify(_load_json(STATIONS_FILE, []))


@app.route('/api/stations', methods=['POST'])
def api_save_stations():
    data = request.get_json(force=True)
    _save_json(STATIONS_FILE, data)
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# API – Cities
# ---------------------------------------------------------------------------

@app.route('/api/cities', methods=['GET'])
def api_get_cities():
    return jsonify(_load_json(CITIES_FILE, []))


@app.route('/api/cities', methods=['POST'])
def api_save_cities():
    data = request.get_json(force=True)
    _save_json(CITIES_FILE, data)
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# API – Layout
# ---------------------------------------------------------------------------

@app.route('/api/layout', methods=['GET'])
def api_get_layout():
    return jsonify(_load_json(LAYOUT_FILE, {}))


@app.route('/api/layout', methods=['POST'])
def api_save_layout():
    data = request.get_json(force=True)
    _save_json(LAYOUT_FILE, data)
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# API – Playback
# ---------------------------------------------------------------------------

@app.route('/api/play', methods=['POST'])
def api_play():
    global _current_station
    data = request.get_json(force=True)
    station_id = data.get('id')

    stations = _load_json(STATIONS_FILE, [])
    station = next((s for s in stations if s['id'] == station_id), None)
    if not station:
        return jsonify({'error': 'Station not found'}), 404

    with _mpv_lock:
        _current_station = station
        _start_mpv(station['url'])

    return jsonify({'ok': True, 'station': station})


@app.route('/api/stop', methods=['POST'])
def api_stop():
    global _current_station
    with _mpv_lock:
        _stop_mpv()
        _current_station = None
    return jsonify({'ok': True})


@app.route('/api/volume', methods=['POST'])
def api_volume():
    global _current_volume
    data = request.get_json(force=True)
    vol = int(data.get('volume', 80))
    vol = max(0, min(100, vol))
    _current_volume = vol
    _send_mpv_command({'command': ['set_property', 'volume', vol]})
    return jsonify({'ok': True, 'volume': vol})


@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({
        'playing': _current_station is not None,
        'station': _current_station,
        'volume': _current_volume,
    })


# ---------------------------------------------------------------------------
# API – Search (Radio Browser)
# ---------------------------------------------------------------------------

RADIO_BROWSER_BASE = 'https://de1.api.radio-browser.info/json'


@app.route('/api/search', methods=['GET'])
def api_search():
    q = request.args.get('q', '').strip()
    city = request.args.get('city', '').strip()

    params = {
        'limit': 24,
        'hidebroken': 'true',
        'order': 'clickcount',
        'reverse': 'true',
    }
    if q:
        params['name'] = q
    if city:
        params['state'] = city

    try:
        resp = requests.get(
            f'{RADIO_BROWSER_BASE}/stations/search',
            params=params,
            timeout=10,
            headers={'User-Agent': 'NashvilleRadio/1.0'},
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502

    results = []
    for s in raw:
        url = s.get('url_resolved') or s.get('url', '')
        if not url:
            continue
        tags = s.get('tags', '') or ''
        genre = tags.split(',')[0].strip().title() if tags else ''
        results.append({
            'id': s.get('stationuuid', ''),
            'name': s.get('name', '').strip(),
            'slogan': s.get('tags', '').replace(',', ' · ')[:60],
            'genre': genre,
            'city': (s.get('state') or s.get('country') or '').strip(),
            'url': url,
            'color': '#e8420a',
        })

    return jsonify(results)


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('static', path)


# ---------------------------------------------------------------------------
# Entry point (dev only — prod uses gunicorn)
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

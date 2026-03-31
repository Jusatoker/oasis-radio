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
SXM_CREDS_FILE = '/data/siriusxm.json'
MPV_SOCKET = '/tmp/mpv-socket'

_current_station = None
_current_volume = 80
_mpv_process = None
_mpv_lock = threading.Lock()

_sxm_client = None
_sxm_process = None

try:
    from sxm.client import SXMClient
    SXM_AVAILABLE = True
except ImportError:
    SXM_AVAILABLE = False


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
    alsa_card = os.environ.get('ALSA_CARD', '0')
    cmd = [
        'mpv',
        f'--input-ipc-server={MPV_SOCKET}',
        f'--volume={_current_volume}',
        '--no-video',
        '--really-quiet',
        '--cache=yes',
        '--cache-secs=5',
        f'--ao=alsa',
        f'--audio-device=alsa/hw:{alsa_card},0',
        url,
    ]
    _mpv_process = subprocess.Popen(cmd, env=env,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# SXM helpers
# ---------------------------------------------------------------------------

def _stop_sxm_proxy():
    global _sxm_process
    if _sxm_process is not None:
        try:
            _sxm_process.terminate()
            _sxm_process.wait(timeout=3)
        except Exception:
            try:
                _sxm_process.kill()
            except Exception:
                pass
        _sxm_process = None


def _start_sxm_proxy(username: str, password: str):
    global _sxm_process
    _stop_sxm_proxy()
    _sxm_process = subprocess.Popen(
        ['python3', '-m', 'sxm.cli', username, password,
         '--port', '9999', '--host', '0.0.0.0'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _sxm_is_logged_in() -> bool:
    return os.path.exists(SXM_CREDS_FILE)


def _autostart_sxm():
    """Called at startup — re-launch proxy if credentials exist."""
    if os.path.exists(SXM_CREDS_FILE):
        creds = _load_json(SXM_CREDS_FILE, {})
        username = creds.get('username', '')
        password = creds.get('password', '')
        if username and password:
            _start_sxm_proxy(username, password)


# Hard-coded channel list that mirrors what sxm-player exposes.
# If the sxm library is importable we use it directly; otherwise fall back.
_SXM_CHANNEL_FALLBACK = [
    {'id': 'siriushits1', 'name': 'Hits 1', 'genre': 'Pop'},
    {'id': 'thebeat', 'name': 'The Beat', 'genre': 'Hip-Hop'},
    {'id': 'octane', 'name': 'Octane', 'genre': 'Rock'},
    {'id': 'lithium', 'name': 'Lithium', 'genre': "90's Alt"},
    {'id': '1stwave', 'name': '1st Wave', 'genre': 'New Wave'},
    {'id': 'altcountry', 'name': 'The Highway', 'genre': 'Country'},
    {'id': 'siriuscountry', 'name': 'Prime Country', 'genre': 'Classic Country'},
    {'id': 'willies', 'name': "Willie's Roadhouse", 'genre': 'Country'},
    {'id': 'classicvinyl', 'name': 'Classic Vinyl', 'genre': 'Classic Rock'},
    {'id': 'classicrewind', 'name': 'Classic Rewind', 'genre': 'Classic Rock'},
    {'id': 'thebridge', 'name': 'The Bridge', 'genre': 'Soft Rock'},
    {'id': 'jazzcat', 'name': 'Real Jazz', 'genre': 'Jazz'},
    {'id': 'soultown', 'name': 'Soul Town', 'genre': 'Soul/R&B'},
    {'id': 'thevibe', 'name': 'The Vibe', 'genre': 'R&B'},
    {'id': 'siriusflyby', 'name': 'FlyBy', 'genre': 'Pop'},
    {'id': 'bpmradio', 'name': 'BPM', 'genre': 'Dance/EDM'},
    {'id': 'electricarea', 'name': 'Electric Area', 'genre': 'EDM'},
    {'id': 'cinemagic', 'name': 'Cinemagic', 'genre': 'Soundtracks'},
    {'id': 'pops', 'name': 'Symphony Hall', 'genre': 'Classical'},
    {'id': 'metrocast', 'name': 'Met Opera Radio', 'genre': 'Opera'},
    {'id': 'latinpop', 'name': 'Caliente', 'genre': 'Latin Pop'},
    {'id': 'reggaeton', 'name': 'Pitbull\'s Globalization', 'genre': 'Reggaeton'},
    {'id': 'radiorythme', 'name': 'Radio Rythme', 'genre': 'French'},
    {'id': 'kidsstuff', 'name': "Kid's Stuff", 'genre': 'Kids'},
    {'id': 'hadithhits', 'name': 'Backspin', 'genre': 'Old School Hip-Hop'},
    {'id': 'ch18', 'name': 'Turbo', 'genre': 'Dance'},
    {'id': 'ch22', 'name': 'Shade 45', 'genre': 'Hip-Hop'},
    {'id': 'ch26', 'name': 'Hip-Hop Nation', 'genre': 'Hip-Hop'},
    {'id': 'ch33', 'name': 'The Pulse', 'genre': 'Pop Hits'},
    {'id': 'ch36', 'name': 'Pop2K', 'genre': '2000s Pop'},
]


def _get_sxm_channels():
    """Return list of SXM channel dicts. Uses library if available."""
    if SXM_AVAILABLE:
        try:
            creds = _load_json(SXM_CREDS_FILE, {})
            username = creds.get('username', '')
            password = creds.get('password', '')
            client = SXMClient(username, password)
            raw_channels = client.channels
            channels = []
            for ch in raw_channels:
                ch_id = getattr(ch, 'channel_id', None) or getattr(ch, 'id', '')
                name = getattr(ch, 'name', '') or str(ch)
                genre = getattr(ch, 'genre_name', '') or ''
                channels.append({
                    'id': ch_id,
                    'name': name,
                    'url': f'http://localhost:9999/{ch_id}/playlist.m3u8',
                    'genre': genre,
                    'color': '#1a1a8c',
                    'source': 'siriusxm',
                    'slogan': genre,
                    'city': 'SiriusXM',
                })
            return channels
        except Exception:
            pass

    # Fallback to hard-coded list
    return [
        {
            'id': ch['id'],
            'name': ch['name'],
            'url': f"http://localhost:9999/{ch['id']}/playlist.m3u8",
            'genre': ch['genre'],
            'color': '#1a1a8c',
            'source': 'siriusxm',
            'slogan': ch['genre'],
            'city': 'SiriusXM',
        }
        for ch in _SXM_CHANNEL_FALLBACK
    ]


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
# API – SiriusXM
# ---------------------------------------------------------------------------

@app.route('/api/sxm/login', methods=['POST'])
def api_sxm_login():
    data = request.get_json(force=True)
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()

    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400

    _save_json(SXM_CREDS_FILE, {'username': username, 'password': password})
    _start_sxm_proxy(username, password)
    return jsonify({'ok': True})


@app.route('/api/sxm/logout', methods=['POST'])
def api_sxm_logout():
    _stop_sxm_proxy()
    try:
        os.unlink(SXM_CREDS_FILE)
    except FileNotFoundError:
        pass
    return jsonify({'ok': True})


@app.route('/api/sxm/status', methods=['GET'])
def api_sxm_status():
    logged_in = _sxm_is_logged_in()
    username = ''
    if logged_in:
        creds = _load_json(SXM_CREDS_FILE, {})
        username = creds.get('username', '')
    return jsonify({'logged_in': logged_in, 'username': username})


@app.route('/api/sxm/channels', methods=['GET'])
def api_sxm_channels():
    if not _sxm_is_logged_in():
        return jsonify({'error': 'Not logged in'}), 401
    return jsonify(_get_sxm_channels())


# ---------------------------------------------------------------------------
# API – Search (Radio Browser + SomaFM + Radio Garden)
# ---------------------------------------------------------------------------

RADIO_BROWSER_BASE = 'https://de1.api.radio-browser.info/json'
SOMAFM_API = 'https://api.somafm.com/channels.json'
RADIO_GARDEN_SEARCH = 'https://radio.garden/api/ara/content/search'
RADIO_GARDEN_LISTEN = 'https://radio.garden/api/ara/content/listen/{id}/channel.mp3'


def _search_radiobrowser(q='', city='', limit=24):
    params = {
        'limit': limit,
        'hidebroken': 'true',
        'order': 'clickcount',
        'reverse': 'true',
    }
    if q:
        params['name'] = q
    if city:
        params['state'] = city

    resp = requests.get(
        f'{RADIO_BROWSER_BASE}/stations/search',
        params=params,
        timeout=10,
        headers={'User-Agent': 'NashvilleRadio/1.0'},
    )
    resp.raise_for_status()
    raw = resp.json()

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
            'source': 'radiobrowser',
        })
    return results


def _search_somafm():
    resp = requests.get(SOMAFM_API, timeout=10,
                        headers={'User-Agent': 'NashvilleRadio/1.0'})
    resp.raise_for_status()
    data = resp.json()

    results = []
    for ch in data.get('channels', []):
        # Prefer HLS, then mp3
        url = ''
        playlists = ch.get('playlists', [])
        for pl in playlists:
            if pl.get('format') == 'aac' and 'url' in pl:
                url = pl['url']
                break
        if not url:
            for pl in playlists:
                if pl.get('format') == 'mp3' and 'url' in pl:
                    url = pl['url']
                    break
        if not url and playlists:
            url = playlists[0].get('url', '')
        if not url:
            continue

        results.append({
            'id': 'somafm-' + ch.get('id', ''),
            'name': ch.get('title', '').strip(),
            'slogan': ch.get('description', '')[:60],
            'genre': ch.get('genre', '').strip().title(),
            'city': 'SomaFM',
            'url': url,
            'color': '#2d2d2d',
            'source': 'somafm',
        })
    return results


def _search_radiogarden(q='', lat='', lng=''):
    params = {}
    if q:
        params['q'] = q
    if lat:
        params['lat'] = lat
    if lng:
        params['lng'] = lng

    resp = requests.get(
        RADIO_GARDEN_SEARCH,
        params=params,
        timeout=10,
        headers={'User-Agent': 'NashvilleRadio/1.0'},
    )
    resp.raise_for_status()
    data = resp.json()

    results = []
    hits = (data.get('hits') or {}).get('hits') or []
    for hit in hits:
        src = hit.get('_source', {})
        ch_id = src.get('channelId') or hit.get('_id', '')
        name = src.get('title', '').strip()
        city_name = (src.get('place') or {}).get('title', '')
        if not ch_id or not name:
            continue
        url = RADIO_GARDEN_LISTEN.format(id=ch_id)
        results.append({
            'id': 'rg-' + ch_id,
            'name': name,
            'slogan': city_name,
            'genre': 'Radio Garden',
            'city': city_name,
            'url': url,
            'color': '#2e7d32',
            'source': 'radiogarden',
        })
    return results


@app.route('/api/search', methods=['GET'])
def api_search():
    q = request.args.get('q', '').strip()
    city = request.args.get('city', '').strip()
    source = request.args.get('source', '').strip().lower()

    if not source:
        source = 'radiobrowser'

    if source == 'radiobrowser':
        try:
            return jsonify(_search_radiobrowser(q=q, city=city))
        except Exception as exc:
            return jsonify({'error': str(exc)}), 502

    if source == 'somafm':
        try:
            return jsonify(_search_somafm())
        except Exception as exc:
            return jsonify({'error': str(exc)}), 502

    if source == 'radiogarden':
        lat = request.args.get('lat', '').strip()
        lng = request.args.get('lng', '').strip()
        try:
            return jsonify(_search_radiogarden(q=q, lat=lat, lng=lng))
        except Exception as exc:
            return jsonify({'error': str(exc)}), 502

    if source == 'all':
        combined = []
        seen_urls = set()

        def _add(items):
            for item in items:
                if item.get('url') and item['url'] not in seen_urls:
                    seen_urls.add(item['url'])
                    combined.append(item)

        try:
            _add(_search_radiobrowser(q=q, city=city))
        except Exception:
            pass
        try:
            _add(_search_somafm())
        except Exception:
            pass
        try:
            _add(_search_radiogarden(q=q))
        except Exception:
            pass

        return jsonify(combined)

    return jsonify({'error': f'Unknown source: {source}'}), 400


@app.route('/api/search/somafm', methods=['GET'])
def api_search_somafm():
    try:
        return jsonify(_search_somafm())
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502


@app.route('/api/search/radiogarden', methods=['GET'])
def api_search_radiogarden():
    q = request.args.get('q', '').strip()
    lat = request.args.get('lat', '').strip()
    lng = request.args.get('lng', '').strip()
    try:
        return jsonify(_search_radiogarden(q=q, lat=lat, lng=lng))
    except Exception as exc:
        return jsonify({'error': str(exc)}), 502


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
    _autostart_sxm()
    app.run(host='0.0.0.0', port=5000, debug=True)
else:
    # Also auto-start when loaded by gunicorn
    _autostart_sxm()

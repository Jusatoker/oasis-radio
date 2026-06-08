"""Server-contract tests for Oasis Radio.

Covers the 'resume last station' feature and mpv URL-scheme hardening.
Run: .venv-test/bin/python -m pytest tests/ -q
"""
import importlib
import json
import os
import sys

import pytest

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")


class _FakeProc:
    """Stand-in for a running mpv process so tests never spawn mpv."""
    returncode = None
    def poll(self): return None
    def terminate(self): pass
    def wait(self, timeout=None): return 0
    def kill(self): pass


@pytest.fixture
def srv(tmp_path, monkeypatch):
    stations = [{"id": "wsix", "name": "WSIX 97.9",
                 "url": "http://stream.example/wsix", "genre": "Country"}]
    (tmp_path / "stations.json").write_text(json.dumps(stations))
    (tmp_path / "cities.json").write_text("[]")
    (tmp_path / "layout.json").write_text("{}")
    monkeypatch.setenv("STATIONS_FILE", str(tmp_path / "stations.json"))
    monkeypatch.setenv("CITIES_FILE", str(tmp_path / "cities.json"))
    monkeypatch.setenv("LAYOUT_FILE", str(tmp_path / "layout.json"))
    monkeypatch.setenv("LAST_FILE", str(tmp_path / "last.json"))
    sys.path.insert(0, APP_DIR)
    import server
    importlib.reload(server)
    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **k: _FakeProc())
    server.app.config.update(TESTING=True)
    return server


def test_status_exposes_last_station_field(srv):
    body = srv.app.test_client().get("/api/status").get_json()
    assert "last" in body and body["last"] is None


def test_play_persists_last_station(srv):
    c = srv.app.test_client()
    assert c.post("/api/play", json={"id": "wsix"}).get_json()["ok"] is True
    playing = c.get("/api/status").get_json()
    assert playing["playing"] is True and playing["station"]["id"] == "wsix"
    c.post("/api/stop")
    stopped = c.get("/api/status").get_json()
    assert stopped["playing"] is False
    assert stopped["last"] is not None and stopped["last"]["id"] == "wsix"


def test_start_mpv_rejects_non_http_urls(srv):
    assert srv._start_mpv("file:///etc/passwd") is False
    assert srv._start_mpv("javascript:alert(1)") is False
    assert srv._start_mpv("http://stream.example/ok") is True
    assert srv._start_mpv("https://stream.example/ok") is True

"""Security and protocol tests for the companion server (the tablet pages). Runs on Linux, Windows and macOS.

Starts the real server on a free localhost port with fake laptop controls, then talks raw HTTP to it.
"""
import http.client
import importlib
import json
import os
import socket
import sys

import pytest

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeControls:
    caps = {"vol_up", "launch"}

    def __init__(self):
        self.app_actions = {"phone_screen": lambda: None}
        self.calls = []

    def state(self):
        return {"caps": sorted(self.caps | set(self.app_actions))}

    def act(self, action, arg=None):
        self.calls.append((action, arg))
        return self.state()


class FakeStats:
    def snapshot(self):
        return {"cpu": 1}


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / ".config").mkdir()
    sys.path.insert(0, APP)
    sys.modules.pop("companion", None)
    companion = importlib.import_module("companion")
    monkeypatch.setattr(companion, "PORT", free_port())
    monkeypatch.setattr(companion, "TOKEN_FILE", str(tmp_path / ".config" / "token"))
    srv = companion.CompanionServer({})
    srv.controls, srv.stats = FakeControls(), FakeStats()
    srv.start()
    yield srv, companion
    srv.stop()
    sys.path.remove(APP)


def request(companion, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", companion.PORT, timeout=5)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def test_token_is_long_random_and_private(server):
    srv, companion = server
    assert len(srv.token) >= 30
    if os.name == "posix":
        assert oct(os.stat(companion.TOKEN_FILE).st_mode & 0o777) == "0o600"


def test_api_needs_the_token(server):
    srv, companion = server
    assert request(companion, "GET", "/api/state")[0] == 403
    assert request(companion, "GET", "/api/state?t=wrong")[0] == 403
    code, _h, body = request(companion, "GET", f"/api/state?t={srv.token}")
    assert code == 200 and json.loads(body)["stats"] == {"cpu": 1}


def test_post_without_token_is_refused_before_reading_the_body(server):
    srv, companion = server
    code = request(companion, "POST", "/api/action", body=b"{}")[0]
    assert code == 403


def test_oversized_or_bad_bodies_are_refused(server):
    srv, companion = server
    url = f"/api/action?t={srv.token}"
    assert request(companion, "POST", url, body=b"x" * 70000)[0] == 413
    code = request(companion, "POST", url, body=b"{}", headers={"Content-Length": "abc"})[0]
    assert code in (400, 413)


def test_only_supported_actions_run_with_clean_arguments(server):
    srv, companion = server
    url = f"/api/action?t={srv.token}"
    for req in ({"id": "rm -rf /"}, {"id": "launch", "arg": "0; reboot"}, {"id": "lock"}):
        assert request(companion, "POST", url, body=json.dumps(req).encode())[0] == 200
    assert srv.controls.calls == []
    request(companion, "POST", url, body=json.dumps({"id": "launch", "arg": "1"}).encode())
    request(companion, "POST", url, body=json.dumps({"id": "vol_up", "arg": "x"}).encode())
    assert srv.controls.calls == [("launch", 1), ("vol_up", None)]


def test_page_has_security_headers(server):
    srv, companion = server
    code, headers, _ = request(companion, "GET", "/")
    h = {k.lower(): v for k, v in headers.items()}
    assert code == 200
    assert h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["cache-control"] == "no-store"


def test_websockets_need_the_token(server):
    srv, companion = server
    s = socket.create_connection(("127.0.0.1", companion.PORT), timeout=5)
    s.sendall(b"GET /ws/pen HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
              b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n")
    assert b"101" not in s.recv(200)
    s.close()


def test_server_listens_on_localhost_only(server):
    srv, companion = server
    host = srv.server.sockets[0].getsockname()[0]
    assert host == "127.0.0.1"

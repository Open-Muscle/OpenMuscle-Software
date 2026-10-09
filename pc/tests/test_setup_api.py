"""Tests for the /api/setup/* routes behind the device setup wizard.

A real uvicorn server runs in a thread on 127.0.0.1 (starlette's TestClient needs
httpx, which is not a dependency), talking to the FakeProvisioningDevice from the
provision client tests through the hidden dev override that stands in for the
device's 192.168.4.1 address. Non-loopback callers are checked by invoking the
route functions with a hand-built Request.
"""

import asyncio
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn
from starlette.requests import Request

from openmuscle.discovery import manager as dmgr
from openmuscle.provision import client as pclient
from openmuscle.web import setup_api
from openmuscle.web.app import create_app

from tests.test_provision_client import FakeProvisioningDevice

PASSWORD = "hunter2-correct-horse"
SSID = "My Home WiFi"
DEVICE_ID = "flexgrid-d7af0b"


# ---- fixtures ---------------------------------------------------------------

def _free_tcp():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _free_udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    def __init__(self, app, port):
        self.app = app
        self.port = port
        self.state = app.state.app_state
        cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info",
                             access_log=True)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 15
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("test server did not start")
            time.sleep(0.05)

    def close(self):
        # The app's broadcaster parks a worker thread on a blocking queue get, so
        # asyncio's executor shutdown keeps the server thread alive for minutes.
        # The thread is a daemon and every test uses its own ports; do not wait.
        self.server.should_exit = True
        self.thread.join(0.2)

    def call(self, method, path, body=None, headers=None):
        """(status, parsed-json-or-None, raw text)."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                status, raw = r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read().decode("utf-8")
        try:
            return status, json.loads(raw), raw
        except ValueError:
            return status, None, raw


@pytest.fixture
def device():
    d = FakeProvisioningDevice(scan_networks=[
        {"ssid": "Far", "rssi": -80, "auth": "wpa2"},
        {"ssid": SSID, "rssi": -40, "auth": "wpa2"},
    ])
    yield d
    d.close()


@pytest.fixture
def web(tmp_path, monkeypatch, device):
    monkeypatch.setattr(dmgr, "default_cache_path", lambda: str(tmp_path / "cache.json"))
    monkeypatch.delenv(setup_api.ENV_AP, raising=False)
    app = create_app(udp_port=_free_udp(), announce_port=_free_udp(),
                     captures_dir=str(tmp_path / "captures"), quiet=True)
    app.state.setup_ap_override = ("127.0.0.1", device.port)
    srv = Server(app, _free_tcp())
    srv.state.discovery.auto_subscribe = False      # no TCP calls to made-up addresses
    yield srv
    srv.close()


def _remote_request(method="GET", origin=None):
    headers = [(b"origin", origin.encode())] if origin else []
    return Request({"type": "http", "method": method, "path": "/", "headers": headers,
                    "client": ("10.0.0.5", 50000), "query_string": b""})


def _route(web, path):
    return next(r for r in web.app.routes if getattr(r, "path", None) == path).endpoint


# ---- page ---------------------------------------------------------------------

def test_setup_page_is_served(web):
    status, _, raw = web.call("GET", "/setup")
    assert status == 200
    assert "Set up a device" in raw
    status, _, js = web.call("GET", "/static/setup/wizard.js")
    assert status == 200 and "OMWizard" in js


# ---- probe / scan ---------------------------------------------------------------

def test_probe_reachable_shape(web, device):
    status, body, _ = web.call("GET", "/api/setup/probe")
    assert status == 200
    assert body["reachable"] is True
    info = body["info"]
    assert info["id"] == DEVICE_ID
    assert info["dev"] == "flexgrid"
    assert info["state"] == "unprovisioned"
    assert info["fw"] == "v4.0.0"
    assert info["ap_name"] == "OM-flexgrid-d7af0b"
    assert "last_wifi_error" in info


def test_probe_unreachable_shape(web, device):
    device.close()
    status, body, _ = web.call("GET", "/api/setup/probe")
    assert status == 200
    assert body["reachable"] is False
    assert body["reason"] == "unreachable"
    assert body["message"]


def test_probe_reports_last_wifi_error(web, device):
    device.settings["last_wifi_error"] = "wrong_password"
    _, body, _ = web.call("GET", "/api/setup/probe")
    assert body["info"]["last_wifi_error"] == "wrong_password"


def test_scan_supported_and_not(web, device):
    status, body, _ = web.call("GET", "/api/setup/scan")
    assert status == 200 and body["supported"] is True
    assert [n["ssid"] for n in body["networks"]] == [SSID, "Far"]
    assert set(body["networks"][0]) == {"ssid", "rssi", "auth"}
    device.scan_networks = None
    _, body, _ = web.call("GET", "/api/setup/scan")
    assert body == {"supported": False, "networks": []}


@pytest.mark.parametrize("host", ["10.0.0.5", "192.168.5.1", "8.8.8.8", "example.com", "127.0.0.1"])
def test_host_is_limited_to_the_setup_network(web, host):
    web.app.state.setup_ap_override = None
    for path in ("/api/setup/probe", "/api/setup/scan"):
        status, body, _ = web.call("GET", path + "?host=" + host)
        assert status == 400, path
        assert body["code"] == "host_not_allowed"
    status, body, _ = web.call("POST", "/api/setup/provision",
                               {"ssid": SSID, "password": PASSWORD, "host": host})
    assert status == 400 and body["code"] == "host_not_allowed"


# ---- provision ------------------------------------------------------------------

def test_provision_success_shape_and_device_state(web, device):
    status, body, _ = web.call("POST", "/api/setup/provision",
                               {"ssid": SSID, "password": PASSWORD, "expect_id": DEVICE_ID})
    assert status == 200
    assert body["ok"] is True
    assert body["id"] == DEVICE_ID
    assert body["reboot_in_ms"] == 1500
    assert body["wait_s"] == 60
    assert isinstance(body["since"], float)
    assert device.settings["wifi_ssid"] == SSID
    assert device.settings["wifi_password"] == PASSWORD
    # The proxy sends exactly {ssid, password}: no hub address of this computer.
    posted = [b for m, p, b in device.requests if m == "POST" and p == "/provision"]
    assert json.loads(posted[0]) == {"ssid": SSID, "password": PASSWORD}


def test_provision_wrong_expected_device_is_409_and_sends_nothing(web, device):
    status, body, _ = web.call("POST", "/api/setup/provision",
                               {"ssid": SSID, "password": PASSWORD, "expect_id": "flexgrid-000000"})
    assert status == 409
    assert body["ok"] is False and body["code"] == "wrong_device"
    assert "wifi_ssid" not in device.settings
    assert not [1 for m, p, b in device.requests if p == "/provision"]


def test_provision_device_not_in_setup_mode(web, device):
    device.state = "provisioned"
    status, body, _ = web.call("POST", "/api/setup/provision", {"ssid": SSID, "password": PASSWORD})
    assert status == 409
    assert body["ok"] is False and body["code"] == "not_in_setup_mode"
    assert body["message"]


def test_provision_unreachable_device(web, device):
    device.close()
    status, body, _ = web.call("POST", "/api/setup/provision", {"ssid": SSID, "password": PASSWORD})
    assert status == 502
    assert body["ok"] is False and body["code"] == "unreachable"


def test_provision_validation_codes(web, device):
    status, body, _ = web.call("POST", "/api/setup/provision", {"ssid": SSID, "password": "short"})
    assert status == 400 and body["code"] == "password_length_invalid"
    status, body, _ = web.call("POST", "/api/setup/provision", {"ssid": "x" * 40, "password": PASSWORD})
    assert status == 400 and body["code"] == "ssid_length_invalid"
    assert "wifi_ssid" not in device.settings


def test_provision_field_length_limits_do_not_echo_the_input(web, device):
    secret = "P" * 300
    status, body, raw = web.call("POST", "/api/setup/provision", {"ssid": SSID, "password": secret})
    assert status == 400 and body["code"] == "invalid_request"
    assert secret not in raw
    status, body, raw = web.call("POST", "/api/setup/provision", {"ssid": "S" * 200, "password": PASSWORD})
    assert status == 400 and body["code"] == "invalid_request"
    assert PASSWORD not in raw
    status, body, _ = web.call("POST", "/api/setup/provision", {"ssid": SSID, "password": PASSWORD,
                                                                 "expect_id": "e" * 200})
    assert status == 400 and body["code"] == "invalid_request"
    status, body, _ = web.call("POST", "/api/setup/provision", {"password": PASSWORD})
    assert status == 400 and body["code"] == "invalid_request"
    assert device.requests == [] or all(p != "/provision" for _, p, _ in device.requests)


def test_provision_garbage_body(web):
    req = urllib.request.Request("http://127.0.0.1:%d/api/setup/provision" % web.port,
                                 data=b"{not json", method="POST",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=10)
    assert ei.value.code == 400
    assert json.loads(ei.value.read())["code"] == "invalid_request"


# ---- loopback only --------------------------------------------------------------

ROUTES = [
    ("/api/setup/probe", lambda f, r: f(r)),
    ("/api/setup/scan", lambda f, r: f(r)),
    ("/api/setup/wifi-hint", lambda f, r: f(r)),
    ("/api/setup/wait", lambda f, r: f(r, device_id="x")),
    ("/api/setup/provision", lambda f, r: f(r)),
    ("/api/setup/reprovision", lambda f, r: f(r)),
]


@pytest.mark.parametrize("path,invoke", ROUTES)
def test_non_loopback_client_gets_403(web, path, invoke):
    from fastapi import HTTPException
    endpoint = _route(web, path)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(invoke(endpoint, _remote_request()))
    assert ei.value.status_code == 403


@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1", True), ("::1", True), ("::ffff:127.0.0.1", True),
    ("10.0.0.5", False), ("192.168.4.2", False), ("::ffff:10.0.0.5", False),
    ("", False), ("testclient", False),
])
def test_is_loopback(host, ok):
    assert setup_api.is_loopback(host) is ok


def test_foreign_origin_is_refused_even_from_loopback(web):
    for origin in ("https://evil.example", "http://192.168.1.9:8000", "null"):
        status, body, _ = web.call("GET", "/api/setup/probe", headers={"Origin": origin})
        assert status == 403, origin
    status, body, _ = web.call("GET", "/api/setup/probe",
                               headers={"Origin": "http://localhost:%d" % web.port})
    assert status == 200


# ---- wifi hint ------------------------------------------------------------------

NETSH = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    GUID                   : 11111111-2222-3333-4444-555555555555
    Physical address       : aa:bb:cc:dd:ee:ff
    State                  : connected
    SSID                   : Home Net 2.4
    BSSID                  : 11:22:33:44:55:66
    Network type           : Infrastructure
    Radio type             : 802.11n
    Channel                : 6
"""

NETSH_DOWN = """
    Name                   : Wi-Fi
    State                  : disconnected
"""

NMCLI = "no:Neighbour\nyes:Cafe\\: Free\nno:Other\n"
NMCLI_NONE = "no:Neighbour\nno:Other\n"


def test_parse_netsh():
    assert setup_api.parse_netsh(NETSH) == "Home Net 2.4"
    assert setup_api.parse_netsh(NETSH_DOWN) is None
    assert setup_api.parse_netsh("") is None


def test_parse_nmcli():
    assert setup_api.parse_nmcli(NMCLI) == "Cafe: Free"
    assert setup_api.parse_nmcli(NMCLI_NONE) is None


def test_parse_networksetup():
    assert setup_api.parse_networksetup("Current Wi-Fi Network: Studio 5G\n") == "Studio 5G"
    assert setup_api.parse_networksetup("You are not associated with an AirPort network.\n") is None
    assert setup_api.parse_networksetup("Current Wi-Fi Network: <redacted>") is None


def test_wifi_hint_on_all_three_os_paths():
    seen = []

    def run_for(table):
        def run(cmd):
            seen.append(cmd[0])
            return table[cmd[0]](cmd)
        return run

    assert setup_api.wifi_hint("win32", run_for({"netsh": lambda c: NETSH})) == "Home Net 2.4"
    assert setup_api.wifi_hint("linux", run_for({"nmcli": lambda c: NMCLI})) == "Cafe: Free"
    mac = {"networksetup": lambda c: ("Current Wi-Fi Network: Studio\n" if c[-1] == "en1"
                                      else "You are not associated with an AirPort network.\n")}
    assert setup_api.wifi_hint("darwin", run_for(mac)) == "Studio"
    assert seen == ["netsh", "nmcli", "networksetup", "networksetup"]


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_wifi_hint_returns_none_without_raising(platform):
    def boom(cmd):
        raise FileNotFoundError(cmd[0])
    assert setup_api.wifi_hint(platform, boom) is None
    assert setup_api.wifi_hint(platform, lambda c: "") is None


def test_wifi_hint_route(web, monkeypatch):
    monkeypatch.setattr(setup_api, "wifi_hint", lambda: "Home Net 2.4")
    assert web.call("GET", "/api/setup/wifi-hint")[1] == {"ssid": "Home Net 2.4"}
    monkeypatch.setattr(setup_api, "wifi_hint", lambda: None)
    assert web.call("GET", "/api/setup/wifi-hint")[1] == {"ssid": None}


# ---- logs -----------------------------------------------------------------------

def test_ssid_and_password_never_reach_the_logs(web, device):
    captured = []

    class Grab(logging.Handler):
        def emit(self, record):
            try:
                captured.append(record.getMessage())
            except Exception:
                pass

    grab = Grab()
    names = ("uvicorn", "uvicorn.access", "uvicorn.error", "openmuscle", "")
    for n in names:
        logging.getLogger(n).addHandler(grab)
    try:
        # Make sure the INFO-level records are produced.
        logging.getLogger().setLevel(logging.INFO)
        web.call("GET", "/api/setup/probe")
        web.call("GET", "/api/setup/scan")
        status, body, _ = web.call("POST", "/api/setup/provision",
                                   {"ssid": SSID, "password": PASSWORD, "expect_id": DEVICE_ID})
        assert status == 200
        web.call("POST", "/api/setup/provision", {"ssid": SSID, "password": "x"})
        # A library line that carries the details is scrubbed before it is stored.
        logging.getLogger("some.library").info("joining %s with %s", SSID, PASSWORD)
        logging.getLogger("some.library").info("body was {\"ssid\": \"Other Net\", \"password\": \"abc12345\"}")
    finally:
        for n in names:
            logging.getLogger(n).removeHandler(grab)
    buffered = [e["message"] for e in web.state.log_buffer.entries(limit=1000)]
    assert any("sent Wi-Fi details to flexgrid-d7af0b" in m for m in buffered)
    assert any("/api/setup/provision" in m for m in captured), "access log was not exercised"
    blob = "\n".join(buffered + captured)
    assert PASSWORD not in blob
    assert SSID not in "\n".join(buffered)
    assert "abc12345" not in "\n".join(buffered)
    assert "Other Net" not in "\n".join(buffered)


def test_scrub_filter_units():
    f = setup_api.SecretScrubFilter()
    f.add("My Home WiFi", "hunter2-correct-horse", "ab")      # too short to register
    assert f.scrub("joining My Home WiFi pw hunter2-correct-horse ab") == "joining *** pw *** ab"
    assert f.scrub("password=abc12345&x=1") == "password=***&x=1"
    assert f.scrub('{"ssid": "Other Net"}') == '{"ssid": "***"}'
    assert f.scrub("nothing to hide here") == "nothing to hide here"
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "pw %s", ("hunter2-correct-horse",), None)
    assert f.filter(rec) is True
    assert "hunter2" not in rec.getMessage()


# ---- wait -----------------------------------------------------------------------

def _announce(web, ip="10.0.0.23"):
    web.state.discovery.on_announce({"id": DEVICE_ID, "dev": "flexgrid", "fw": "v4.0.0",
                                     "services": {"cmd": 8001, "sensor": 3141}}, ip)


def test_wait_sees_a_device_that_announces_after_the_send(web, device):
    status, body, _ = web.call("POST", "/api/setup/provision", {"ssid": SSID, "password": PASSWORD})
    since = body["since"]
    threading.Timer(0.8, _announce, args=(web,)).start()
    t0 = time.time()
    status, res, _ = web.call("GET", "/api/setup/wait?device_id=%s&timeout=5&since=%s" % (DEVICE_ID, since))
    assert status == 200
    assert res["found"] is True
    assert res["device"]["ip"] == "10.0.0.23"
    assert res["device"]["device_id"] == DEVICE_ID
    assert time.time() - t0 < 10


def test_wait_ignores_a_sighting_from_before_the_send(web, device):
    _announce(web)                       # stale cache entry from before
    time.sleep(0.1)
    _, body, _ = web.call("POST", "/api/setup/provision", {"ssid": SSID, "password": PASSWORD})
    _, res, _ = web.call("GET", "/api/setup/wait?device_id=%s&timeout=1&since=%s" % (DEVICE_ID, body["since"]))
    assert res["found"] is False
    # Without `since` the server uses the time of its own provision call.
    _, res, _ = web.call("GET", "/api/setup/wait?device_id=%s&timeout=1" % DEVICE_ID)
    assert res["found"] is False
    _announce(web)
    _, res, _ = web.call("GET", "/api/setup/wait?device_id=%s&timeout=1" % DEVICE_ID)
    assert res["found"] is True


def test_wait_caps_the_timeout_and_validates(web):
    t0 = time.time()
    _, res, _ = web.call("GET", "/api/setup/wait?device_id=nobody&timeout=0")
    assert res["found"] is False and time.time() - t0 < 3
    status, body, _ = web.call("GET", "/api/setup/wait?device_id=")
    assert status == 400 and body["code"] == "invalid_request"


# ---- reprovision ----------------------------------------------------------------

def test_reprovision_uses_the_discovered_address(web, monkeypatch):
    calls = []

    def fake(host, port=80, timeout=5):
        calls.append((host, port))
        return pclient.ProvisionResult(device_id=DEVICE_ID, reboot_in_ms=1500)

    monkeypatch.setattr(pclient, "reprovision", fake)
    _announce(web, "10.0.0.23")
    status, body, _ = web.call("POST", "/api/setup/reprovision", {"device_id": DEVICE_ID})
    assert status == 200
    assert body == {"ok": True, "id": DEVICE_ID, "reboot_in_ms": 1500}
    assert calls == [("10.0.0.23", 80)]


def test_reprovision_unknown_or_public_device(web, monkeypatch):
    monkeypatch.setattr(pclient, "reprovision", lambda *a, **k: pytest.fail("must not be called"))
    status, body, _ = web.call("POST", "/api/setup/reprovision", {"device_id": "nobody"})
    assert status == 404 and body["code"] == "unknown_device"
    _announce(web, "8.8.8.8")
    status, body, _ = web.call("POST", "/api/setup/reprovision", {"device_id": DEVICE_ID})
    assert status == 400 and body["code"] == "host_not_allowed"


def test_reprovision_against_the_fake_device(web, device):
    _announce(web, "127.0.0.1")
    web.app.state.setup_ap_override = ("127.0.0.1", device.port)
    status, body, _ = web.call("POST", "/api/setup/reprovision", {"device_id": DEVICE_ID})
    assert status == 200 and body["ok"] is True
    assert ("POST", "/reprovision", b"") in device.requests

"""Tests for the /api/setup/usb/* routes: Wi-Fi setup over the band's USB cable.

A real uvicorn server runs on 127.0.0.1 (as in test_setup_api.py). The USB side
is the fake band from tests/usb_fakes.py behind usb.py's two seams, so the whole
chain from the HTTP route down to the line protocol is exercised without opening
a serial port.
"""

import asyncio
import logging
import threading
import time

import pytest

from openmuscle.discovery import manager as dmgr
from openmuscle.provision import usb
from openmuscle.web import setup_api
from openmuscle.web.app import create_app

from tests.test_setup_api import Server, _free_tcp, _free_udp, _remote_request, _route
from tests.usb_fakes import BAND_ID, PASSWORD, SSID, FakeBand, install_bench


@pytest.fixture
def bench(monkeypatch):
    monkeypatch.setattr(usb, "COMMAND_TIMEOUT_S", 0.3)
    monkeypatch.setattr(setup_api, "USB_PROBE_TIMEOUT_S", 0.3)
    return install_bench(monkeypatch)


@pytest.fixture
def web(tmp_path, monkeypatch, bench):
    monkeypatch.setattr(dmgr, "default_cache_path", lambda: str(tmp_path / "cache.json"))
    monkeypatch.delenv(setup_api.ENV_AP, raising=False)
    app = create_app(udp_port=_free_udp(), announce_port=_free_udp(),
                     captures_dir=str(tmp_path / "captures"), quiet=True)
    srv = Server(app, _free_tcp())
    srv.state.discovery.auto_subscribe = False
    yield srv
    srv.close()


def _provision(web, **over):
    body = {"port": "COM5", "ssid": SSID, "password": PASSWORD}
    body.update(over)
    return web.call("POST", "/api/setup/usb/provision", body)


def _announce(web, ip="10.0.0.23"):
    web.state.discovery.on_announce({"id": BAND_ID, "dev": "flexgrid", "fw": "v4.0.0",
                                     "services": {"cmd": 8001, "sensor": 3141}}, ip)


# ---- ports ------------------------------------------------------------------------

def test_ports_empty_when_nothing_is_plugged_in(web):
    status, body, _ = web.call("GET", "/api/setup/usb/ports")
    assert status == 200 and body == {"ports": []}


def test_ports_lists_a_band_with_its_identity(web, bench):
    bench.add("COM5", FakeBand(role="right", state="provisioned", wifi_ssid="Home"))
    status, body, _ = web.call("GET", "/api/setup/usb/ports")
    assert status == 200
    assert body["ports"] == [{"port": "COM5", "description": "USB Serial", "answered": True,
                              "id": BAND_ID, "role": "right", "state": "provisioned",
                              "fw": "1.2.3", "wifi_ssid": "Home"}]


def test_ports_lists_several_and_a_silent_one(web, bench):
    bench.add("COM5", FakeBand())
    bench.add("COM6", FakeBand(band_id="flexgrid-000006", role="left"))
    bench.add("COM7", FakeBand(silent=True))
    _, body, _ = web.call("GET", "/api/setup/usb/ports")
    got = {p["port"]: p for p in body["ports"]}
    assert set(got) == {"COM5", "COM6", "COM7"}
    assert got["COM6"]["id"] == "flexgrid-000006" and got["COM6"]["answered"] is True
    assert got["COM7"]["answered"] is False and got["COM7"]["id"] == ""


def test_ports_explains_a_missing_serial_library(web, monkeypatch):
    def boom():
        raise usb.UsbSetupError("no_port", "The USB part of OpenMuscle is not installed.")
    monkeypatch.setattr(usb, "_comports", boom)
    status, body, _ = web.call("GET", "/api/setup/usb/ports")
    assert status == 200 and body["ports"] == [] and body["reason"] == "no_port"
    assert "not installed" in body["message"]


# ---- provision --------------------------------------------------------------------

def test_provision_sends_the_details_and_returns_the_wait_handles(web, bench):
    band = bench.add("COM5", FakeBand(noise=["I (12) log noise"]))
    t0 = time.time()
    status, body, _ = _provision(web, expect_id=BAND_ID)
    assert status == 200
    assert body["ok"] is True and body["id"] == BAND_ID and body["role"] == "left"
    assert body["wait_s"] == 60 and body["reboot_in_ms"] == 1000
    assert body["since"] >= t0 + setup_api.USB_RESTART_GRACE_S - 0.1
    assert band.received[-1]["data"] == {"verb": "set_wifi", "ssid": SSID, "password": PASSWORD}


def test_the_wait_route_finds_the_band_once_it_announces_on_the_lan(web, bench):
    bench.add("COM5", FakeBand())
    _, body, _ = _provision(web)
    threading.Timer(2.0, _announce, args=(web,)).start()
    _, res, _ = web.call("GET", "/api/setup/wait?device_id=%s&timeout=8&since=%s"
                         % (BAND_ID, body["since"]))
    assert res["found"] is True and res["device"]["ip"] == "10.0.0.23"


def test_a_beacon_in_the_second_before_the_reboot_does_not_count(web, bench):
    bench.add("COM5", FakeBand(state="provisioned"))
    _, body, _ = _provision(web)
    _announce(web, "10.0.0.9")
    _, res, _ = web.call("GET", "/api/setup/wait?device_id=%s&timeout=0.5&since=%s"
                         % (BAND_ID, body["since"]))
    assert res["found"] is False


def test_provision_refuses_a_port_the_os_does_not_list_as_a_band(web, bench):
    band = bench.add("COM5", FakeBand())
    status, body, _ = _provision(web, port="COM99")
    assert status == 404 and body["ok"] is False and body["code"] == "no_port"
    status, body, _ = _provision(web, port="/dev/null")
    assert status == 404 and body["code"] == "no_port"
    assert band.opened == 0


def test_provision_wrong_band_is_409_and_sends_nothing(web, bench):
    band = bench.add("COM5", FakeBand())
    status, body, _ = _provision(web, expect_id="flexgrid-000000")
    assert status == 409 and body["code"] == "wrong_device" and BAND_ID in body["message"]
    assert [c["data"]["verb"] for c in band.received] == ["get_info"]


def test_provision_validation_uses_the_same_codes_as_the_other_route(web, bench):
    band = bench.add("COM5", FakeBand())
    status, body, _ = _provision(web, password="short")
    assert status == 400 and body["code"] == "password_length_invalid"
    status, body, _ = _provision(web, ssid="x" * 40)
    assert status == 400 and body["code"] == "ssid_length_invalid"
    assert band.opened == 0


def test_provision_field_limits_do_not_echo_the_input(web, bench):
    bench.add("COM5", FakeBand())
    secret = "P" * 300
    status, body, raw = _provision(web, password=secret)
    assert status == 400 and body["code"] == "invalid_request" and secret not in raw
    status, body, _ = web.call("POST", "/api/setup/usb/provision", {"ssid": SSID})
    assert status == 400 and body["code"] == "invalid_request"          # no port


def test_provision_band_refusal_is_422_with_its_code(web, bench):
    bench.add("COM5", FakeBand(errors={"set_wifi": ("save_failed", "flash")}))
    status, body, raw = _provision(web)
    assert status == 422 and body["ok"] is False and body["code"] == "rejected"
    assert body["band_code"] == "save_failed" and "could not save" in body["message"]
    assert PASSWORD not in raw


def test_provision_silent_band_says_replug(web, bench):
    bench.add("COM5", FakeBand(silent=True))
    status, body, _ = _provision(web)
    assert status == 504 and body["code"] == "no_answer"
    assert "plug it back in (or press the BOOT button)" in body["message"]


def test_provision_port_that_will_not_open_is_no_port(web, bench):
    bench.add("COM5", FakeBand())
    bench.fail_open.add("COM5")
    status, body, _ = _provision(web)
    assert status == 404 and body["code"] == "no_port"


# ---- guards -------------------------------------------------------------------------

USB_ROUTES = [
    ("/api/setup/usb/ports", lambda f, r: f(r)),
    ("/api/setup/usb/provision", lambda f, r: f(r)),
]


@pytest.mark.parametrize("path,invoke", USB_ROUTES)
def test_non_loopback_client_gets_403(web, path, invoke):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(invoke(_route(web, path), _remote_request()))
    assert ei.value.status_code == 403


def test_foreign_origin_and_host_are_refused(web, bench):
    band = bench.add("COM5", FakeBand())
    for origin in ("https://evil.example", "http://192.168.1.9:8000", "null"):
        status, _, _ = web.call("GET", "/api/setup/usb/ports", headers={"Origin": origin})
        assert status == 403, origin
        status, _, _ = web.call("POST", "/api/setup/usb/provision",
                                {"port": "COM5", "ssid": SSID, "password": PASSWORD},
                                headers={"Origin": origin})
        assert status == 403, origin
    for host in ("attacker.example:%d" % web.port, "192.168.1.9:8000"):
        status, _, _ = web.call("GET", "/api/setup/usb/ports", headers={"Host": host})
        assert status == 403, host
    assert band.opened == 0


# ---- logs ---------------------------------------------------------------------------

def test_ssid_and_password_never_reach_the_logs(web, bench):
    bench.add("COM5", FakeBand(noise=["echo " + PASSWORD]))
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
        logging.getLogger().setLevel(logging.INFO)
        status, _, _ = _provision(web)
        assert status == 200
        _provision(web, password="x")
    finally:
        for n in names:
            logging.getLogger(n).removeHandler(grab)
    buffered = [e["message"] for e in web.state.log_buffer.entries(limit=1000)]
    assert any("sent Wi-Fi details to %s over USB" % BAND_ID in m for m in buffered)
    assert any("/api/setup/usb/provision" in m for m in captured), "access log not exercised"
    blob = "\n".join(buffered + captured)
    assert PASSWORD not in blob
    assert SSID not in blob

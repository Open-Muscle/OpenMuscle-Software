"""Tests for openmuscle.provision.client against a fake device server.

FakeProvisioningDevice is an asyncio server that mimics the firmware's
lib/provisioning.py _handle(): one reader.read(2048) per connection, the same
_parse_request / _json_body helpers, the same routes and error messages, and
Connection: close. Two switches model the firmware before and after the
Content-Length fix:

    single_read=True   one read only (today's firmware): a body that arrives in
                       a later segment is lost (empty body, so ssid_length_invalid)
    single_read=False  reads until Content-Length bytes arrive (hardened firmware)
"""

import asyncio
import json
import logging
import socket
import struct
import threading
import time

import pytest

from openmuscle.provision import client as pc
from openmuscle.provision import (
    Credentials, ProvisionError, ap_ssid_for, get_info, probe_ap, provision,
    reprovision, scan, validate_credentials, wait_for_device,
)

PASSWORD = "hunter2-correct-horse"
SSID = "My Home WiFi"


# ---- fake device -----------------------------------------------------------

def _parse_request(raw):
    """Same shape as the firmware's parser."""
    head_end = raw.find(b"\r\n\r\n")
    if head_end < 0:
        head_end = raw.find(b"\n\n")
        if head_end < 0:
            raise ValueError("incomplete request head")
        sep_len = 2
    else:
        sep_len = 4
    head = raw[:head_end].decode("utf-8", "replace")
    body = raw[head_end + sep_len:]
    lines = head.split("\n")
    parts = lines[0].strip().split(" ")
    if len(parts) < 2:
        raise ValueError("bad request line")
    headers = {}
    for ln in lines[1:]:
        ln = ln.strip()
        if ln and ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return parts[0].upper(), parts[1], headers, body


class FakeProvisioningDevice:
    def __init__(self, state="unprovisioned", single_read=True, scan_networks=None,
                 reset_first=0, drop_all=False, info_override=None, delay_s=0.0):
        self.state = state
        self.single_read = single_read
        self.scan_networks = scan_networks      # None -> /scan is a 404
        self.reset_first = reset_first          # RST this many connections first
        self.drop_all = drop_all                # close every connection with no reply
        self.info_override = info_override      # (status, body_bytes, ctype) for /info
        self.delay_s = delay_s
        self.settings = {}
        self.connections = 0
        self.requests = []                      # (method, path, body_bytes)
        self.raw_requests = []
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._server = None
        self.port = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(5)

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._server = self._loop.run_until_complete(
            asyncio.start_server(self._handle, "127.0.0.1", 0))
        self.port = self._server.sockets[0].getsockname()[1]
        self._ready.set()
        self._loop.run_forever()

    def close(self):
        async def _stop():
            self._server.close()
            await self._server.wait_closed()
        fut = asyncio.run_coroutine_threadsafe(_stop(), self._loop)
        try:
            fut.result(3)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(3)

    # -- helpers
    def _respond(self, writer, code, text, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        writer.write(("HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                      "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
                      % (code, text, ctype, len(b))).encode() + b)

    def _err(self, writer, code, text, message, typ="provision_ack"):
        self._respond(writer, code, text, json.dumps(
            {"v": "1.0", "type": typ, "status": "error", "data": {"message": message}}))

    async def _read_request(self, reader):
        raw = await reader.read(2048)           # the firmware's single read
        if self.single_read or not raw:
            return raw
        # hardened firmware: keep reading until Content-Length bytes are in
        while True:
            sep = raw.find(b"\r\n\r\n")
            if sep >= 0:
                break
            more = await reader.read(2048)
            if not more:
                return raw
            raw += more
        _, _, headers, _ = _parse_request(raw)
        need = int(headers.get("content-length", "0"))
        have = len(raw) - (sep + 4)
        while have < need:
            more = await asyncio.wait_for(reader.read(2048), 3)
            if not more:
                break
            raw += more
            have += len(more)
        return raw

    async def _handle(self, reader, writer):
        self.connections += 1
        n = self.connections
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if self.drop_all:
                return
            if n <= self.reset_first:
                sock = writer.get_extra_info("socket")
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                struct.pack("ii", 1, 0))
                writer.transport.abort()
                return
            raw = await self._read_request(reader)
            if not raw:
                return
            self.raw_requests.append(raw)
            try:
                method, path, headers, body = _parse_request(raw)
            except ValueError as e:
                self._err(writer, 400, "Bad Request", str(e), "error")
                await writer.drain()
                return
            self.requests.append((method, path, body))

            if method == "GET" and path == "/info":
                if self.info_override:
                    code, b, ctype = self.info_override
                    self._respond(writer, code, "X", b, ctype)
                else:
                    self._respond(writer, 200, "OK", json.dumps({
                        "v": "1.0", "type": "info", "id": "flexgrid-d7af0b",
                        "dev": "flexgrid", "fw": "v4.0.0",
                        "caps": ["sensor", "status", "cmd", "imu"],
                        "matrix": [15, 4], "state": self.state,
                        "last_wifi_error": self.settings.get("last_wifi_error", "")}))
            elif method == "GET" and path == "/scan" and self.scan_networks is not None:
                self._respond(writer, 200, "OK", json.dumps(
                    {"v": "1.0", "type": "scan",
                     "data": {"networks": self.scan_networks}}))
            elif method == "POST" and path == "/provision":
                await self._provision(writer, headers, body)
            elif method == "POST" and path == "/reprovision":
                self.settings["wifi_ssid"] = ""
                self._respond(writer, 200, "OK", json.dumps(
                    {"v": "1.0", "type": "reprovision_ack", "status": "ok",
                     "reboot_in_ms": 1500}))
            else:
                self._respond(writer, 404, "Not Found", json.dumps(
                    {"status": "error", "data": {"message": "no_route",
                                                 "method": method, "path": path}}))
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _provision(self, writer, headers, body):
        if self.state == "provisioned":
            self._err(writer, 403, "Forbidden", "not_in_setup_mode")
            return
        if self.single_read is False and len(body) < int(headers.get("content-length", "0")):
            self._err(writer, 400, "Bad Request", "incomplete_body")
            return
        try:
            req = json.loads(body.decode("utf-8")) if body else {}
        except Exception as e:
            self._err(writer, 400, "Bad Request", "invalid_json: {}".format(e))
            return
        ssid = (req.get("ssid") or "")
        password = req.get("password") or ""
        if not (1 <= len(ssid.encode("utf-8")) <= 32):
            self._err(writer, 400, "Bad Request", "ssid_length_invalid (1..32)")
            return
        if password and not (8 <= len(password.encode("utf-8")) <= 63):
            self._err(writer, 400, "Bad Request",
                      "password_length_invalid (8..63 for WPA2, or empty)")
            return
        self.settings["wifi_ssid"] = ssid
        self.settings["wifi_password"] = password
        for k in ("hub_host", "hub_port"):
            if req.get(k):
                self.settings[k] = req[k]
        self._respond(writer, 200, "OK", json.dumps(
            {"v": "1.0", "type": "provision_ack", "status": "ok",
             "id": "flexgrid-d7af0b", "next": "switching_to_sta",
             "reboot_in_ms": 1500}))


@pytest.fixture
def device():
    d = FakeProvisioningDevice()
    yield d
    d.close()


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch):
    monkeypatch.setattr(pc, "RETRY_DELAY_S", 0.01)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _split_post(port, body, gap=0.3):
    """http.client style: header and body as two separate sends."""
    head = ("POST /provision HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body)).encode()
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    out = b""
    try:
        s.sendall(head)
        time.sleep(gap)
        s.sendall(body)
        while True:
            c = s.recv(4096)
            if not c:
                break
            out += c
    except ConnectionError:
        pass          # the device already answered and closed; the reply may be lost
    finally:
        s.close()
    return out


# ---- /info -----------------------------------------------------------------

def test_get_info_success(device):
    info = get_info("127.0.0.1", port=device.port)
    assert info.device_id == "flexgrid-d7af0b"
    assert info.device_type == "flexgrid"
    assert info.fw == "v4.0.0"
    assert info.matrix == [15, 4]
    assert info.unprovisioned
    assert ap_ssid_for(info) == "OM-flexgrid-d7af0b"


def test_probe_ap_uses_given_host(device):
    info = probe_ap("127.0.0.1", port=device.port)
    assert info.device_id == "flexgrid-d7af0b"


def test_info_reports_last_wifi_error(device):
    device.settings["last_wifi_error"] = "wrong_password"
    assert get_info("127.0.0.1", port=device.port).last_wifi_error == "wrong_password"


def test_ap_ssid_for_lask5_dict():
    assert ap_ssid_for({"id": "lask5-01", "dev": "lask5"}) == "OM-lask5-01"


def test_unreachable_host_refused():
    with pytest.raises(ProvisionError) as ei:
        get_info("127.0.0.1", port=_free_port(), timeout=1.0)
    assert ei.value.code == "unreachable"
    assert "Cannot reach the device" in str(ei.value)


def test_unreachable_when_nothing_replies():
    # accepts then closes without a word, forever: retry once, then give up
    d = FakeProvisioningDevice(drop_all=True)
    try:
        with pytest.raises(ProvisionError) as ei:
            get_info("127.0.0.1", port=d.port, timeout=1.0)
        assert ei.value.code == "unreachable"
        assert d.connections == 2          # original + one retry
    finally:
        d.close()


def test_unreachable_on_read_timeout():
    d = FakeProvisioningDevice(delay_s=2.0)
    try:
        with pytest.raises(ProvisionError) as ei:
            get_info("127.0.0.1", port=d.port, timeout=0.3)
        assert ei.value.code == "unreachable"
    finally:
        d.close()


def test_not_openmuscle_html():
    d = FakeProvisioningDevice(info_override=(200, b"<html>router login</html>", "text/html"))
    try:
        with pytest.raises(ProvisionError) as ei:
            get_info("127.0.0.1", port=d.port)
        assert ei.value.code == "not_openmuscle"
    finally:
        d.close()


def test_not_openmuscle_json_without_type_info():
    d = FakeProvisioningDevice(info_override=(200, b'{"hello": "world"}', "application/json"))
    try:
        with pytest.raises(ProvisionError) as ei:
            get_info("127.0.0.1", port=d.port)
        assert ei.value.code == "not_openmuscle"
    finally:
        d.close()


def test_not_openmuscle_404():
    d = FakeProvisioningDevice(info_override=(404, b"nope", "text/plain"))
    try:
        with pytest.raises(ProvisionError) as ei:
            get_info("127.0.0.1", port=d.port)
        assert ei.value.code == "not_openmuscle"
    finally:
        d.close()


# ---- /scan -----------------------------------------------------------------

def test_scan_sorted_by_rssi():
    nets = [{"ssid": "far", "rssi": -80, "auth": "wpa2"},
            {"ssid": "near", "rssi": -40, "auth": "wpa2"},
            {"ssid": "mid", "rssi": -60, "auth": "open"}]
    d = FakeProvisioningDevice(scan_networks=nets)
    try:
        out = scan("127.0.0.1", port=d.port)
    finally:
        d.close()
    assert [n["ssid"] for n in out] == ["near", "mid", "far"]
    assert out[1]["auth"] == "open"


def test_scan_404_returns_none(device):
    assert scan("127.0.0.1", port=device.port) is None


# ---- /provision ------------------------------------------------------------

def test_provision_success_persists_and_acks(device):
    res = provision("127.0.0.1", SSID, PASSWORD, port=device.port)
    assert res.device_id == "flexgrid-d7af0b"
    assert res.reboot_in_ms == 1500
    assert res.next_step == "switching_to_sta"
    assert device.settings["wifi_ssid"] == SSID          # spaces preserved, not stripped
    assert device.settings["wifi_password"] == PASSWORD


def test_provision_body_is_exactly_ssid_and_password(device):
    provision("127.0.0.1", SSID, PASSWORD, port=device.port)
    method, path, body = device.requests[-1]
    assert (method, path) == ("POST", "/provision")
    assert json.loads(body) == {"ssid": SSID, "password": PASSWORD}


def test_provision_headers(device):
    provision("127.0.0.1", SSID, PASSWORD, port=device.port)
    head, _, body = device.raw_requests[-1].partition(b"\r\n\r\n")
    h = head.decode().lower()
    assert "content-length: %d" % len(body) in h
    assert "connection: close" in h
    assert "content-type: application/json" in h


def test_provision_hub_fields_only_when_given(device):
    provision("127.0.0.1", SSID, PASSWORD, hub_host="10.0.0.5", hub_port=3141,
              port=device.port)
    assert json.loads(device.requests[-1][2]) == {
        "ssid": SSID, "password": PASSWORD, "hub_host": "10.0.0.5", "hub_port": 3141}
    assert device.settings["hub_host"] == "10.0.0.5"


def test_provision_open_network_empty_password(device):
    provision("127.0.0.1", "CafeOpen", "", port=device.port)
    assert device.settings["wifi_password"] == ""


def test_provision_uses_one_sendall(device, monkeypatch):
    sends = []
    real = pc._connect

    class Spy:
        def __init__(self, s):
            self._s = s

        def sendall(self, data):
            sends.append(("sendall", len(data)))
            return self._s.sendall(data)

        def send(self, data):
            sends.append(("send", len(data)))
            return self._s.send(data)

        def __getattr__(self, name):
            return getattr(self._s, name)

    monkeypatch.setattr(pc, "_connect", lambda *a, **k: Spy(real(*a, **k)))
    provision("127.0.0.1", SSID, PASSWORD, port=device.port)
    assert len(sends) == 1 and sends[0][0] == "sendall"
    assert sends[0][1] == len(device.raw_requests[-1])    # header and body in one write


def test_split_segment_breaks_firmware_single_read_but_not_this_client(device):
    body = json.dumps({"ssid": SSID, "password": PASSWORD}).encode()
    _split_post(device.port, body)                       # the http.client behaviour
    assert device.requests[-1] == ("POST", "/provision", b"")   # body never read
    assert "wifi_ssid" not in device.settings            # nothing was persisted
    provision("127.0.0.1", SSID, PASSWORD, port=device.port)
    assert device.settings["wifi_ssid"] == SSID           # one-write client succeeds


def test_split_segment_on_hardened_firmware():
    d = FakeProvisioningDevice(single_read=False)
    try:
        body = json.dumps({"ssid": SSID, "password": PASSWORD}).encode()
        reply = _split_post(d.port, body, gap=0.5)
        assert b'"status": "ok"' in reply
        assert d.settings["wifi_ssid"] == SSID
    finally:
        d.close()


def test_provision_twenty_in_a_row(device):
    for _ in range(20):
        provision("127.0.0.1", SSID, PASSWORD, port=device.port)
    assert device.settings["wifi_ssid"] == SSID


def test_retry_once_on_connection_reset():
    d = FakeProvisioningDevice(reset_first=1)
    try:
        res = provision("127.0.0.1", SSID, PASSWORD, port=d.port)
        assert res.device_id == "flexgrid-d7af0b"
        assert d.connections == 2
        assert d.settings["wifi_ssid"] == SSID
    finally:
        d.close()


def test_gives_up_after_second_reset():
    d = FakeProvisioningDevice(reset_first=5)
    try:
        with pytest.raises(ProvisionError) as ei:
            provision("127.0.0.1", SSID, PASSWORD, port=d.port)
        assert ei.value.code == "unreachable"
        assert d.connections == 2
    finally:
        d.close()


def test_provision_unreachable():
    with pytest.raises(ProvisionError) as ei:
        provision("127.0.0.1", SSID, PASSWORD, port=_free_port(), timeout=1.0)
    assert ei.value.code == "unreachable"


# ---- error codes -----------------------------------------------------------

def test_ssid_length_client_side_and_device_side(device):
    for bad in ("", "x" * 33, "é" * 17):            # 17 two-byte chars = 34 bytes
        with pytest.raises(ProvisionError) as ei:
            provision("127.0.0.1", bad, PASSWORD, port=device.port)
        assert ei.value.code == "ssid_length_invalid"
        assert str(ei.value) == "The Wi-Fi name must be 1 to 32 characters"
    assert device.connections == 0                       # caught before the network
    with pytest.raises(ProvisionError) as ei:            # same mapping from the device
        provision("127.0.0.1", "x" * 33, PASSWORD, port=device.port, validate=False)
    assert ei.value.code == "ssid_length_invalid"
    assert ei.value.status == 400
    assert str(ei.value) == "The Wi-Fi name must be 1 to 32 characters"


def test_password_length_client_side_and_device_side(device):
    for bad in ("short", "p" * 64):
        with pytest.raises(ProvisionError) as ei:
            provision("127.0.0.1", SSID, bad, port=device.port)
        assert ei.value.code == "password_length_invalid"
        assert str(ei.value) == "The password must be 8 to 63 characters"
    assert device.connections == 0
    with pytest.raises(ProvisionError) as ei:
        provision("127.0.0.1", SSID, "short", port=device.port, validate=False)
    assert ei.value.code == "password_length_invalid"
    assert str(ei.value) == "The password must be 8 to 63 characters"


def test_invalid_json_mapped():
    # the firmware's own wording: 'invalid_json: <parser message>'
    d = FakeProvisioningDevice()
    try:
        status, _, rbody = pc._request("127.0.0.1", "POST", "/provision", body=b"{nope",
                                       port=d.port)
        with pytest.raises(ProvisionError) as ei:
            pc._raise_for_device_error("127.0.0.1", status, pc._json(rbody))
        assert ei.value.code == "invalid_json"
        assert "try again" in str(ei.value).lower()
    finally:
        d.close()


def test_incomplete_body_mapped():
    body = json.dumps({"status": "error", "data": {"message": "incomplete_body"}}).encode()
    with pytest.raises(ProvisionError) as ei:
        pc._raise_for_device_error("h", 400, pc._json(body))
    assert ei.value.code == "incomplete_body"
    assert ei.value.status == 400


def test_not_in_setup_mode():
    d = FakeProvisioningDevice(state="provisioned")
    try:
        with pytest.raises(ProvisionError) as ei:
            provision("127.0.0.1", SSID, PASSWORD, port=d.port)
        assert ei.value.code == "not_in_setup_mode"
        assert ei.value.status == 403
        assert "already connected" in str(ei.value)
        assert "wifi_ssid" not in d.settings
    finally:
        d.close()


def test_payload_too_large_413():
    body = b'{"status":"error","data":{"message":"body_too_large"}}'
    with pytest.raises(ProvisionError) as ei:
        pc._raise_for_device_error("h", 413, pc._json(body))
    assert ei.value.code == "payload_too_large"


def test_unknown_device_error_is_generic_not_crash():
    body = b'{"status":"error","data":{"message":"flux_capacitor_failed"}}'
    with pytest.raises(ProvisionError) as ei:
        pc._raise_for_device_error("h", 500, pc._json(body))
    assert ei.value.code == "device_error"
    assert "flux_capacitor_failed" in str(ei.value)


def test_provision_200_non_json_is_not_openmuscle():
    class Html(FakeProvisioningDevice):
        async def _provision(self, writer, headers, body):
            self._respond(writer, 200, "OK", "<html>hi</html>", "text/html")

    d = Html()
    try:
        with pytest.raises(ProvisionError) as ei:
            provision("127.0.0.1", SSID, PASSWORD, port=d.port)
        assert ei.value.code == "not_openmuscle"
    finally:
        d.close()


# ---- /reprovision ----------------------------------------------------------

def test_reprovision_success():
    d = FakeProvisioningDevice(state="provisioned")
    try:
        d.settings["wifi_ssid"] = "home"
        res = reprovision("127.0.0.1", port=d.port)
        assert res.reboot_in_ms == 1500
        assert d.settings["wifi_ssid"] == ""
        assert d.requests[-1][:2] == ("POST", "/reprovision")
    finally:
        d.close()


def test_reprovision_unreachable():
    with pytest.raises(ProvisionError) as ei:
        reprovision("127.0.0.1", port=_free_port(), timeout=1.0)
    assert ei.value.code == "unreachable"


# ---- secrecy ---------------------------------------------------------------

def test_password_never_in_logs_reprs_or_errors(device, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []
    provision("127.0.0.1", SSID, PASSWORD, port=device.port)
    # failure paths: device-side error, client-side error, reset-and-retry
    for kwargs in ({"password": "short"}, {"password": "short", "validate": False}):
        try:
            provision("127.0.0.1", SSID, port=device.port, **kwargs)
        except ProvisionError as e:
            seen += [str(e), repr(e), e.detail, str(e.__dict__)]
    d2 = FakeProvisioningDevice(state="provisioned", reset_first=1)
    try:
        try:
            provision("127.0.0.1", SSID, PASSWORD, port=d2.port)
        except ProvisionError as e:
            seen += [str(e), repr(e), e.detail, str(e.__dict__)]
    finally:
        d2.close()
    try:
        provision("127.0.0.1", SSID, PASSWORD, port=_free_port(), timeout=1.0)
    except ProvisionError as e:
        seen += [str(e), repr(e), e.detail, str(e.__dict__)]
    seen += [r.getMessage() for r in caplog.records]
    seen += [str(r.__dict__) for r in caplog.records]
    creds = Credentials(SSID, PASSWORD)
    seen += [repr(creds), str(creds)]
    assert caplog.records, "expected some log output to inspect"
    for text in seen:
        assert PASSWORD not in text
    assert "***" in repr(creds) and SSID in repr(creds)


def test_result_and_info_reprs_hold_no_password(device):
    res = provision("127.0.0.1", SSID, PASSWORD, port=device.port)
    assert PASSWORD not in repr(res)
    assert PASSWORD not in repr(get_info("127.0.0.1", port=device.port))


def test_validate_credentials_direct():
    validate_credentials("ok", "")                       # open network
    validate_credentials("a" * 32, "p" * 63)
    with pytest.raises(ProvisionError):
        validate_credentials("a" * 32, "p" * 7)


# ---- wait_for_device -------------------------------------------------------

class FakeDiscovery:
    def __init__(self):
        self.devices = []

    def snapshot(self):
        return list(self.devices)


def test_wait_for_device_returns_when_it_appears():
    disc = FakeDiscovery()
    since = time.time()

    def later():
        time.sleep(0.3)
        disc.devices.append({"device_id": "flexgrid-d7af0b", "ip": "10.0.0.7",
                             "age_s": 0.1})

    threading.Thread(target=later, daemon=True).start()
    got = wait_for_device("flexgrid-d7af0b", timeout=5, discovery=disc, poll_s=0.05,
                          since=since)
    assert got["ip"] == "10.0.0.7"


def test_wait_for_device_ignores_stale_cache_entry():
    disc = FakeDiscovery()
    disc.devices.append({"device_id": "flexgrid-d7af0b", "ip": "10.0.0.7",
                         "age_s": 3600.0})
    with pytest.raises(ProvisionError) as ei:
        wait_for_device("flexgrid-d7af0b", timeout=0.3, discovery=disc, poll_s=0.05)
    assert ei.value.code == "join_timeout"


def test_wait_for_device_ignores_other_devices():
    disc = FakeDiscovery()
    disc.devices.append({"device_id": "lask5-01", "ip": "10.0.0.9", "age_s": 0.0})
    with pytest.raises(ProvisionError) as ei:
        wait_for_device("flexgrid-d7af0b", timeout=0.2, discovery=disc, poll_s=0.05)
    assert ei.value.code == "join_timeout"
    assert "Wi-Fi" in str(ei.value)


def test_wait_for_device_requires_discovery():
    with pytest.raises(ValueError):
        wait_for_device("x", timeout=0.1)

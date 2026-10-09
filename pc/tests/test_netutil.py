"""Network helpers: exclusive binds, address lookup, Wi-Fi and adapter parsing."""

import socket
import threading
import time

import pytest

from openmuscle import netutil
from openmuscle.receiver.udp_listener import ListenerBindError, UDPListener


def hold_udp_port(host="0.0.0.0"):
    """Bind a UDP port exclusively and keep it, like a second program would."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    netutil.make_exclusive(s)
    s.bind((host, 0))
    return s, s.getsockname()[1]


# ---- exclusive UDP binds ----------------------------------------------------

def test_udp_bind_check_free_then_busy():
    s, port = hold_udp_port()
    try:
        ok, why = netutil.udp_bind_check(port)
        assert ok is False
        assert str(port) in why and "already in use" in why
    finally:
        s.close()
    ok, why = netutil.udp_bind_check(port)
    assert ok is True and why == ""


def test_listener_reports_busy_port_instead_of_dying():
    s, port = hold_udp_port()
    try:
        listener = UDPListener(port=port, quiet=True)
        listener.start()
        assert listener.bound is False
        assert str(port) in listener.bind_error
        assert listener.health()["ok"] is False
        assert listener.health()["error"] == listener.bind_error
    finally:
        s.close()


def test_listener_strict_raises_one_sentence():
    s, port = hold_udp_port()
    try:
        with pytest.raises(ListenerBindError) as ei:
            UDPListener(port=port, quiet=True, strict=True).start()
        assert "already in use" in str(ei.value)
    finally:
        s.close()


def test_second_listener_on_same_port_cannot_share_it():
    first = UDPListener(port=0, bind_ip="127.0.0.1", quiet=True)
    first.start()
    assert first.bound is True
    try:
        second = UDPListener(port=first.bound_port, bind_ip="127.0.0.1", quiet=True)
        second.start()
        assert second.bound is False
        # and the first one still receives
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(b'{"v":"1.0","type":"flexgrid","id":"fg-x","ts":1,'
                  b'"data":{"matrix":[[0,0,0,0]]}}', ("127.0.0.1", first.bound_port))
        tx.close()
        deadline = time.time() + 2
        while first.packet_queue.empty() and time.time() < deadline:
            time.sleep(0.02)
        assert first.packet_queue.get_nowait().device_id == "fg-x"
        assert first.health()["packets_total"] >= 1
        assert first.health()["last_packet_age_s"] is not None
    finally:
        first.stop()


def test_tcp_port_free_sees_a_listener():
    s = socket.socket()
    netutil.make_exclusive(s)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert netutil.tcp_port_free("127.0.0.1", port) is False
    finally:
        s.close()
    assert netutil.tcp_port_free("127.0.0.1", port) is True


def test_bind_error_text_is_a_plain_sentence():
    e = OSError(98, "Address already in use")
    assert "already in use" in netutil.bind_error_text(3141, "UDP", e)
    assert "3141" in netutil.bind_error_text(3141, "UDP", e)


# ---- recognising an OpenMuscle server ----------------------------------------

def _serve_json(body, delay=0.0):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if delay:
                time.sleep(delay)
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_find_running_instance_recognises_openmuscle():
    srv = _serve_json('{"debug": false, "app": "openmuscle", "version": "9"}')
    try:
        info = netutil.find_running_instance(srv.server_address[1])
        assert info["app"] == "openmuscle"
    finally:
        srv.shutdown()


def test_find_running_instance_accepts_older_servers_without_app_field():
    srv = _serve_json('{"debug": false}')
    try:
        assert netutil.find_running_instance(srv.server_address[1]) is not None
    finally:
        srv.shutdown()


def test_find_running_instance_ignores_other_programs():
    srv = _serve_json('{"hello": "world"}')
    try:
        assert netutil.find_running_instance(srv.server_address[1]) is None
    finally:
        srv.shutdown()
        srv.server_close()
    assert netutil.find_running_instance(srv.server_address[1]) is None


# ---- addresses --------------------------------------------------------------

@pytest.mark.parametrize("ip,ok", [
    ("192.168.4.2", True), ("10.0.0.23", True), ("127.0.0.1", False),
    ("0.0.0.0", False), ("169.254.3.4", False), ("", False), (None, False),
    ("1.2.3", False), ("a.b.c.d", False), ("300.1.1.1", False)])
def test_is_usable_ipv4(ip, ok):
    assert netutil.is_usable_ipv4(ip) is ok


def test_local_ip_for_loopback_is_loopback():
    assert netutil.local_ip_for("127.0.0.1") == "127.0.0.1"


def test_default_lan_ip_uses_the_route_not_a_fixed_gateway(monkeypatch):
    asked = []

    def fake(target, port=9):
        asked.append(target)
        return "192.168.4.77" if target == "1.1.1.1" else None

    monkeypatch.setattr(netutil, "local_ip_for", fake)
    assert netutil.default_lan_ip() == "192.168.4.77"
    assert "8.8.8.8" in asked and "1.1.1.1" in asked


def test_default_lan_ip_falls_back_to_loopback(monkeypatch):
    monkeypatch.setattr(netutil, "local_ip_for", lambda *a, **k: None)

    def boom(_name):
        raise OSError("no resolver")

    monkeypatch.setattr(socket, "gethostbyname", boom)
    assert netutil.default_lan_ip() == "127.0.0.1"


def test_subnet_of():
    assert netutil.subnet_of("192.168.4.9") == "192.168.4"
    assert netutil.subnet_of("127.0.0.1") is None


# ---- Wi-Fi name and adapter parsing --------------------------------------------

NETSH_WLAN = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    State                  : connected
    SSID                   : Home Net 5
    BSSID                  : aa:bb:cc:dd:ee:ff
    Network type           : Infrastructure
"""


def test_parse_wifi_windows_ignores_bssid():
    assert netutil.parse_wifi_ssid("windows", NETSH_WLAN) == "Home Net 5"
    assert netutil.parse_wifi_ssid("windows", "There is no wireless interface") is None


def test_parse_wifi_macos():
    assert netutil.parse_wifi_ssid(
        "darwin", "Current Wi-Fi Network: Studio\n") == "Studio"
    assert netutil.parse_wifi_ssid(
        "darwin", "You are not associated with an AirPort network.") is None


def test_parse_wifi_linux_nmcli():
    out = "no:Neighbour\nyes:Cafe\\: Free\nno:Other\n"
    assert netutil.parse_wifi_ssid("linux", out) == "Cafe: Free"
    assert netutil.parse_wifi_ssid("linux", "no:Neighbour\n") is None


IPCONFIG = """
Windows IP Configuration

Ethernet adapter Ethernet:

   Media State . . . . . . . . . . . : Media disconnected

Wireless LAN adapter Wi-Fi:

   IPv4 Address. . . . . . . . . . . : 192.168.4.2
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
"""


def test_parse_adapter_windows():
    assert netutil.parse_adapter_for_ip("windows", IPCONFIG, "192.168.4.2") == "Wi-Fi"
    assert netutil.parse_adapter_for_ip("windows", IPCONFIG, "10.9.9.9") is None


def test_parse_adapter_linux_and_macos():
    assert netutil.parse_adapter_for_ip(
        "linux", "2: wlan0    inet 192.168.1.5/24 brd 192.168.1.255 scope global\n",
        "192.168.1.5") == "wlan0"
    mac = "en0: flags=8863<UP>\n\tinet 192.168.1.9 netmask 0xffffff00\nlo0: flags=1\n"
    assert netutil.parse_adapter_for_ip("darwin", mac, "192.168.1.9") == "en0"


def test_rate_window_counts_the_last_minute_only():
    r = netutil.RateWindow()
    now = 10_000.0
    for i in range(5):
        r.add(now=now + i)
    assert r.per_minute(now=now + 5) == 5
    assert r.per_minute(now=now + 70) == 0
    r.add(now=now + 70)
    assert r.per_minute(now=now + 70) == 1

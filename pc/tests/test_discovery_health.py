"""Discovery that survives real networks: a PC address per device, the active
discover probe, listener health, and the wider network search.

Uses the same fake V4 command server as test_discovery.py.
"""

import json
import socket
import threading
import time

import pytest

from openmuscle import netutil
from openmuscle.discovery import manager as mgr_mod
from openmuscle.discovery.manager import DiscoveryManager
from tests.test_discovery import FakeV4CmdServer, _announce

import openmuscle.discovery.manager as manager_module


@pytest.fixture
def server():
    s = FakeV4CmdServer()
    yield s
    s.stop()


def _wait(pred, timeout=3.0, step=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(step)
    return pred()


def _hold_udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    netutil.make_exclusive(s)
    s.bind(("0.0.0.0", 0))
    return s, s.getsockname()[1]


# ---- the address a device is told ----------------------------------------------

def test_band_on_the_bands_own_network_is_told_that_network(monkeypatch, tmp_path):
    """A band on 192.168.4.x is told a 192.168.4.x address, never 127.x."""
    asked = []

    def route(target, port=9):
        asked.append(target)
        return "192.168.4.2" if target.startswith("192.168.4.") else "10.0.0.5"

    monkeypatch.setattr(manager_module, "local_ip_for", route)
    m = DiscoveryManager(cache_path=str(tmp_path / "c.json"), auto_subscribe=False)
    assert m.pc_host_for("192.168.4.1") == "192.168.4.2"
    assert m.pc_host_for("10.0.0.23") == "10.0.0.5"
    assert "192.168.4.1" in asked


def test_a_remote_band_never_hears_loopback(monkeypatch, tmp_path):
    monkeypatch.setattr(manager_module, "local_ip_for", lambda t, p=9: "127.0.0.1")
    monkeypatch.setattr(manager_module, "default_lan_ip", lambda *a, **k: "127.0.0.1")
    m = DiscoveryManager(cache_path=str(tmp_path / "c.json"), auto_subscribe=False)
    assert m.pc_host_for("192.168.4.1") == ""        # no usable address, not 127.x
    assert m.pc_host_for("127.0.0.1") == "127.0.0.1"  # a simulator on this computer


def test_no_route_falls_back_to_the_main_address(monkeypatch, tmp_path):
    monkeypatch.setattr(manager_module, "local_ip_for", lambda t, p=9: None)
    m = DiscoveryManager(pc_host="10.0.0.9", cache_path=str(tmp_path / "c.json"),
                         auto_subscribe=False)
    # pinned wins outright
    assert m.pc_host_for("192.168.4.1") == "10.0.0.9"


def test_subscribe_sends_the_address_toward_that_device(server, monkeypatch, tmp_path):
    monkeypatch.setattr(manager_module, "local_ip_for", lambda t, p=9: "192.168.4.2")
    m = DiscoveryManager(cache_path=str(tmp_path / "c.json"), auto_subscribe=True)
    try:
        m.on_announce(_announce(device_id="flexgrid-test01", cmd=server.port),
                      "127.0.0.1")
        assert _wait(lambda: ("192.168.4.2", 3141) in server.subscribers)
        assert m.status()["pc_host_by_device"]["flexgrid-test01"] == "192.168.4.2"
    finally:
        m.stop()


def test_heartbeat_carries_the_new_address_after_the_computer_moves(
        server, monkeypatch, tmp_path):
    here = {"ip": "192.168.1.20"}
    monkeypatch.setattr(manager_module, "local_ip_for", lambda t, p=9: here["ip"])
    monkeypatch.setattr(manager_module, "HEARTBEAT_S", 0.1)
    m = DiscoveryManager(cache_path=str(tmp_path / "c.json"), auto_subscribe=True)
    try:
        m.on_announce(_announce(device_id="flexgrid-test01", cmd=server.port),
                      "127.0.0.1")

        def beats(host):
            return [d for v, d in server.received
                    if v == "heartbeat" and d.get("host") == host]

        assert _wait(lambda: beats("192.168.1.20"))
        here["ip"] = "10.0.0.77"             # laptop joined another network
        assert _wait(lambda: beats("10.0.0.77"))
    finally:
        m.stop()


def test_resubscribe_recomputes_the_address(server, monkeypatch, tmp_path):
    here = {"ip": "192.168.1.20"}
    monkeypatch.setattr(manager_module, "local_ip_for", lambda t, p=9: here["ip"])
    m = DiscoveryManager(cache_path=str(tmp_path / "c.json"), auto_subscribe=False)
    try:
        m.on_announce(_announce(device_id="flexgrid-test01", cmd=server.port),
                      "127.0.0.1")
        assert m.subscribe("flexgrid-test01")
        assert ("192.168.1.20", 3141) in server.subscribers
        m.unsubscribe("flexgrid-test01")
        here["ip"] = "10.0.0.77"
        assert m.subscribe("flexgrid-test01")
        assert ("10.0.0.77", 3141) in server.subscribers
    finally:
        m.stop()


def test_subscribe_with_no_address_fails_readably(server, monkeypatch, tmp_path):
    monkeypatch.setattr(manager_module, "local_ip_for", lambda t, p=9: "127.0.0.1")
    monkeypatch.setattr(manager_module, "default_lan_ip", lambda *a, **k: "127.0.0.1")
    m = DiscoveryManager(cache_path=str(tmp_path / "c.json"), auto_subscribe=False)
    try:
        m.on_announce(_announce(device_id="flexgrid-far", cmd=server.port),
                      "192.168.4.1")
        assert m.subscribe("flexgrid-far") is False
        snap = m.snapshot()[0]
        assert "no address" in snap["sub_error"]
    finally:
        m.stop()


def test_no_hardcoded_gateway_in_the_address_lookup():
    import inspect
    assert "10.0.0.1" not in inspect.getsource(mgr_mod)
    # a few routing hints are tried in turn; none is required to exist
    assert len(netutil._ROUTE_HINTS) > 1


# ---- the discover probe ------------------------------------------------------

class FakeDiscoverSource:
    """A device that answers {"verb":"discover"} like the firmware does: a unicast
    announce back to the prober. It never sends a beacon on its own."""

    def __init__(self, device_id="flexgrid-probe1", cmd=8001):
        self.device_id = device_id
        self.cmd = cmd
        self.probes = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.2)
        self._run = True
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while self._run:
            try:
                data, addr = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data)
            except ValueError:
                continue
            if msg.get("type") == "cmd" and msg.get("data", {}).get("verb") == "discover":
                self.probes += 1
                ann = _announce(device_id=self.device_id, cmd=self.cmd)
                self.sock.sendto(json.dumps(ann).encode(), addr)

    def stop(self):
        self._run = False
        self.sock.close()


def test_discover_probe_finds_a_silent_source_within_a_second(tmp_path):
    src = FakeDiscoverSource()
    m = DiscoveryManager(pc_host="127.0.0.1", announce_port=src.port,
                         cache_path=str(tmp_path / "c.json"), auto_subscribe=False,
                         discover_targets=["127.0.0.1"])
    try:
        assert m.snapshot() == []                      # no beacon was ever heard
        t0 = time.time()
        found = m.send_discover()
        assert time.time() - t0 < 1.0
        assert found == ["flexgrid-probe1"]
        snap = m.snapshot()
        assert snap[0]["device_id"] == "flexgrid-probe1"
        assert snap[0]["ip"] == "127.0.0.1"
        assert snap[0]["source"] == "beacon"
        assert src.probes == 1
        assert m.status()["last_announce_s"] is not None
        assert m.status()["last_discover_found"] == 1
    finally:
        m.stop()
        src.stop()


def test_discover_probe_with_nobody_answering_returns_empty(tmp_path):
    s, port = _hold_udp()          # something is there but never answers
    m = DiscoveryManager(pc_host="127.0.0.1", announce_port=port,
                         cache_path=str(tmp_path / "c.json"), auto_subscribe=False,
                         discover_targets=["127.0.0.1"])
    try:
        assert m.send_discover(wait_s=0.3) == []
        assert m.status()["last_discover_s"] is not None
    finally:
        m.stop()
        s.close()


def test_discover_reaches_two_sources(tmp_path):
    a, b = FakeDiscoverSource("flexgrid-a1"), FakeDiscoverSource("flexgrid-b2")
    # Two answerers cannot share one port on one machine, so probe each in turn.
    m = DiscoveryManager(pc_host="127.0.0.1", announce_port=a.port,
                         cache_path=str(tmp_path / "c.json"), auto_subscribe=False,
                         discover_targets=["127.0.0.1"])
    try:
        assert m.send_discover() == ["flexgrid-a1"]
        m.announce_port = b.port
        assert m.send_discover() == ["flexgrid-b2"]
        assert {d["device_id"] for d in m.snapshot()} == {"flexgrid-a1", "flexgrid-b2"}
    finally:
        m.stop()
        a.stop()
        b.stop()


def test_probe_targets_include_limited_broadcast_and_local_networks(monkeypatch, tmp_path):
    monkeypatch.setattr(netutil, "local_ipv4_addresses",
                        lambda: ["192.168.4.2", "10.0.0.5"])
    m = DiscoveryManager(pc_host="10.0.0.5", cache_path=str(tmp_path / "c.json"),
                         auto_subscribe=False)
    assert m.broadcast_targets() == ["255.255.255.255", "192.168.4.255", "10.0.0.255"]


def test_background_watcher_probes_at_start_and_when_the_address_changes(
        monkeypatch, tmp_path):
    ips = {"now": "192.168.1.20"}
    monkeypatch.setattr(manager_module, "default_lan_ip", lambda *a, **k: ips["now"])
    probes = []
    s, port = _hold_udp()
    m = DiscoveryManager(announce_port=port, cache_path=str(tmp_path / "c.json"),
                         auto_subscribe=False, discover_targets=["127.0.0.1"],
                         discover_interval_s=60.0, net_poll_s=0.05)
    real = m.send_discover
    m.send_discover = lambda *a, **k: (probes.append(time.time()), real(0.05))[1]
    try:
        m.start()
        assert _wait(lambda: len(probes) >= 1)          # at startup
        n = len(probes)
        time.sleep(0.3)
        assert len(probes) == n                         # interval is long: quiet
        ips["now"] = "10.0.0.77"
        assert _wait(lambda: len(probes) > n)           # moved: probe again
        assert m.pc_host == "10.0.0.77"
    finally:
        m.stop()
        s.close()


# ---- listener health ---------------------------------------------------------

def test_failed_bind_of_3140_sets_listener_ok_false(tmp_path):
    s, port = _hold_udp()
    m = DiscoveryManager(pc_host="127.0.0.1", announce_port=port,
                         cache_path=str(tmp_path / "c.json"), auto_subscribe=False,
                         discover_targets=[])
    try:
        assert m.listener_ok is None                    # not started yet
        m.start()
        assert m.listener_ok is False
        st = m.status()
        assert st["listener_ok"]["announce"] is False
        assert str(port) in st["listener_errors"]["announce"]
        assert st["ports"]["announce"] == port
    finally:
        m.stop()
        s.close()


def test_good_bind_sets_listener_ok_true(tmp_path):
    probe, port = _hold_udp()
    probe.close()
    m = DiscoveryManager(pc_host="127.0.0.1", announce_port=port,
                         cache_path=str(tmp_path / "c.json"), auto_subscribe=False,
                         discover_targets=[])
    try:
        m.start()
        assert m.listener_ok is True
        assert m.status()["listener_ok"]["announce"] is True
    finally:
        m.stop()


def test_status_reports_the_data_listener_too(tmp_path):
    from openmuscle.receiver.udp_listener import UDPListener
    s, port = _hold_udp()
    bad = UDPListener(port=port, quiet=True)
    bad.start()
    m = DiscoveryManager(pc_host="127.0.0.1", cache_path=str(tmp_path / "c.json"),
                         auto_subscribe=False)
    try:
        st = m.status(data_listener=bad, full=False)
        assert st["listener_ok"]["data"] is False
        assert "already in use" in st["listener_errors"]["data"]
        assert st["interface"] is None                   # full=False skips the lookup
    finally:
        m.stop()
        s.close()


def test_announce_counters(tmp_path):
    m = DiscoveryManager(pc_host="127.0.0.1", cache_path=str(tmp_path / "c.json"),
                         auto_subscribe=False)
    assert m.status(full=False)["last_announce_s"] is None
    m.on_announce(_announce(), "10.0.0.23")
    m.on_announce(_announce(), "10.0.0.23")
    st = m.status(full=False)
    assert st["announces_total"] == 2
    assert st["announces_per_min"] == 2
    assert st["last_announce_s"] < 1.0


# ---- Search my network --------------------------------------------------------

def _scan_probes(m, **kw):
    probed = []
    m.probe = lambda ip, cmd_port=None, timeout=None: probed.append(ip)
    m.scan_subnet(background=False, **kw)
    return probed


def test_scan_default_is_the_own_24(tmp_path):
    m = DiscoveryManager(pc_host="10.0.5.10", cache_path=str(tmp_path / "c.json"),
                         auto_subscribe=False)
    probed = _scan_probes(m)
    assert len(probed) == 253                             # .1-.254 minus self
    assert {ip.rsplit(".", 1)[0] for ip in probed} == {"10.0.5"}


def test_scan_16_is_capped_and_goes_outward_from_here(tmp_path):
    m = DiscoveryManager(pc_host="10.0.5.10", cache_path=str(tmp_path / "c.json"),
                         auto_subscribe=False)
    probed = _scan_probes(m, prefix=16)
    assert len(probed) <= 1024
    blocks = []
    for ip in probed:
        b = ip.rsplit(".", 1)[0]
        if b not in blocks:
            blocks.append(b)
    assert blocks[0] == "10.0.5"                           # own network first
    assert set(blocks[1:3]) == {"10.0.4", "10.0.6"}        # then its neighbours
    assert all(b.startswith("10.0.") for b in blocks)


def test_scan_cap_can_be_lowered(tmp_path):
    m = DiscoveryManager(pc_host="10.0.5.10", cache_path=str(tmp_path / "c.json"),
                         auto_subscribe=False)
    assert len(_scan_probes(m, prefix=16, max_hosts=100)) in (99, 100)  # own IP skipped

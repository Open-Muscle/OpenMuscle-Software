"""Tests for native V4 discovery + subscribe (openmuscle.discovery).

The centerpiece is a fake in-process TCP command server that mimics the
firmware's lib/commands.py dispatch (newline-delimited JSON, {msg_id, data:
{verb,...}} -> ack). It validates the keeper's subscribe/heartbeat/get_info/
unsubscribe message shapes against a server that parses them the way a real V4
device does, with no hardware.
"""

import json
import socket
import threading
import time

import pytest

from openmuscle.discovery.manager import (
    DiscoveryManager, DiscoveredDevice, _send_cmd,
)
from openmuscle.protocol.parser import parse_packet


class FakeV4CmdServer:
    """Minimal stand-in for a V4 device's command channel + subscriber list.

    Speaks the same wire protocol as FlexGridV4-Firmware/lib/commands.py:
    one JSON command per line, one JSON ack per line.
    """

    def __init__(self, device_id="flexgrid-test01", device_type="flexgrid",
                 matrix=(15, 4), max_subscribers=4, accept=True):
        self.device_id = device_id
        self.device_type = device_type
        self.matrix = list(matrix)
        self.max_subscribers = max_subscribers
        self.accept = accept
        self.subscribers = []          # list of (host, port)
        self.received = []             # every (verb, data) we got, for asserts
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self.port = self._srv.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while self._running:
            try:
                self._srv.settimeout(0.5)
                conn, _ = self._srv.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._client, args=(conn,),
                             daemon=True).start()

    def _client(self, conn):
        buf = b""
        try:
            while self._running:
                conn.settimeout(0.5)
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if line:
                        conn.sendall(self._handle(line) + b"\n")
        except OSError:
            pass
        finally:
            conn.close()

    def _handle(self, line):
        try:
            pkt = json.loads(line.decode("utf-8"))
        except Exception as e:
            return self._ack(None, "error", {"message": "invalid_json: %s" % e})
        msg_id = pkt.get("msg_id")
        data = pkt.get("data") or {}
        verb = data.get("verb")
        self.received.append((verb, data))
        if verb == "subscribe":
            host, port = data.get("host"), int(data["port"])
            accepted = self.accept and len(self.subscribers) < self.max_subscribers
            if accepted and (host, port) not in self.subscribers:
                self.subscribers.append((host, port))
            return self._ack(msg_id, "ok", {
                "verb": verb, "accepted": accepted,
                "subscriber_count": len(self.subscribers),
                "max_subscribers": self.max_subscribers})
        if verb == "heartbeat":
            return self._ack(msg_id, "ok", {"verb": verb, "refreshed": True})
        if verb == "unsubscribe":
            host, port = data.get("host"), int(data["port"])
            if (host, port) in self.subscribers:
                self.subscribers.remove((host, port))
            return self._ack(msg_id, "ok", {"verb": verb, "removed": True})
        if verb == "get_info":
            return self._ack(msg_id, "ok", {
                "verb": verb, "id": self.device_id, "dev": self.device_type,
                "fw": "v4.0.0", "matrix": self.matrix, "caps": ["sensor"],
                "subscribers": []})
        return self._ack(msg_id, "error", {"message": "unknown_verb"})

    @staticmethod
    def _ack(msg_id, status, data):
        return (json.dumps({"v": "1.0", "type": "ack", "status": status,
                            "msg_id": msg_id, "data": data})).encode("utf-8")

    def stop(self):
        self._running = False
        try:
            self._srv.close()
        except OSError:
            pass


@pytest.fixture
def server():
    s = FakeV4CmdServer()
    yield s
    s.stop()


@pytest.fixture
def mgr(tmp_path):
    m = DiscoveryManager(pc_host="127.0.0.1", udp_port=3141,
                         cache_path=str(tmp_path / "cache.json"),
                         auto_subscribe=False)
    yield m
    m.stop()


# ---- announce parsing -----------------------------------------------------

def _announce(device_id="flexgrid-d7af0b", dev="flexgrid", cmd=8001):
    return {
        "v": "1.0", "type": "announce", "id": device_id, "role": "source",
        "dev": dev, "fw": "v4.0.0", "transports": ["wifi"], "caps": ["sensor"],
        "matrix": [15, 4], "services": {"sensor": 3141, "cmd": cmd},
        "ts": 12345,
    }


def test_on_announce_creates_device(mgr):
    dev = mgr.on_announce(_announce(), "10.0.0.23")
    assert dev.device_id == "flexgrid-d7af0b"
    assert dev.device_type == "flexgrid"
    assert dev.ip == "10.0.0.23"          # taken from packet source, not payload
    assert dev.cmd_port == 8001
    assert dev.sensor_port == 3141
    assert dev.matrix == [15, 4]
    assert dev.source == "beacon"
    assert mgr.snapshot()[0]["device_id"] == "flexgrid-d7af0b"


def test_on_announce_refreshes_not_duplicates(mgr):
    mgr.on_announce(_announce(), "10.0.0.23")
    mgr.on_announce(_announce(), "10.0.0.99")   # same id, new IP
    snap = mgr.snapshot()
    assert len(snap) == 1
    assert snap[0]["ip"] == "10.0.0.99"


def test_on_announce_ignores_garbage(mgr):
    assert mgr.on_announce({}, "10.0.0.1") is None
    assert mgr.on_announce(None, "10.0.0.1") is None
    assert mgr.snapshot() == []


# ---- parser + announce routing -------------------------------------------

def test_parser_routes_announce_to_data():
    raw = json.dumps(_announce()).encode("utf-8")
    pkt = parse_packet(raw)
    assert pkt.device_type == "announce"
    assert pkt.data["services"]["cmd"] == 8001      # full announce preserved
    assert pkt.data["dev"] == "flexgrid"


def test_parser_sensor_frame_unaffected():
    frame = {"v": "1.0", "type": "flexgrid", "id": "flexgrid-d7af0b",
             "ts": 1, "seq": 5, "data": {"matrix": [[1, 2], [3, 4]]}}
    pkt = parse_packet(json.dumps(frame).encode("utf-8"))
    assert pkt.device_type == "flexgrid"
    assert pkt.data["matrix"] == [[1, 2], [3, 4]]


# ---- cache round-trip -----------------------------------------------------

def test_cache_persists_and_reloads(tmp_path):
    path = str(tmp_path / "c.json")
    m1 = DiscoveryManager(pc_host="127.0.0.1", cache_path=path,
                          auto_subscribe=False)
    m1.on_announce(_announce(device_id="flexgrid-aaa"), "10.0.0.5")
    m1.stop()
    # A fresh manager reads the same cache file.
    m2 = DiscoveryManager(pc_host="127.0.0.1", cache_path=path,
                          auto_subscribe=False)
    cached = m2._load_cache()
    assert any(e["device_id"] == "flexgrid-aaa" and e["ip"] == "10.0.0.5"
               for e in cached)
    m2.stop()


# ---- subscribe / heartbeat handshake against the fake device --------------

def test_subscribe_handshake(server, mgr):
    dev = DiscoveredDevice("flexgrid-test01", "flexgrid", "127.0.0.1",
                           server.port)
    mgr._devices[dev.device_id] = dev
    assert mgr.subscribe(dev.device_id) is True
    assert dev.subscribed is True
    # Server recorded our subscribe with the right host/port/transport.
    verbs = [v for v, _ in server.received]
    assert "subscribe" in verbs
    sub = next(d for v, d in server.received if v == "subscribe")
    assert sub["host"] == "127.0.0.1"
    assert sub["port"] == 3141
    assert sub["transport"] == "wifi"
    assert ("127.0.0.1", 3141) in server.subscribers


def test_heartbeats_flow(server, mgr):
    dev = DiscoveredDevice("flexgrid-test01", "flexgrid", "127.0.0.1",
                           server.port)
    mgr._devices[dev.device_id] = dev
    mgr.subscribe(dev.device_id)
    # Wait for at least two heartbeats (1 Hz).
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if sum(1 for v, _ in server.received if v == "heartbeat") >= 2:
            break
        time.sleep(0.1)
    hb = sum(1 for v, _ in server.received if v == "heartbeat")
    assert hb >= 2, "expected >=2 heartbeats, got %d" % hb


def test_unsubscribe_clean(server, mgr):
    dev = DiscoveredDevice("flexgrid-test01", "flexgrid", "127.0.0.1",
                           server.port)
    mgr._devices[dev.device_id] = dev
    mgr.subscribe(dev.device_id)
    assert ("127.0.0.1", 3141) in server.subscribers
    mgr.unsubscribe(dev.device_id)
    # Give the unsubscribe a moment to land.
    deadline = time.time() + 2.0
    while time.time() < deadline and server.subscribers:
        time.sleep(0.05)
    assert server.subscribers == []
    assert dev.subscribed is False


def test_subscribe_rejected_when_full(mgr):
    server = FakeV4CmdServer(accept=False)
    try:
        dev = DiscoveredDevice("flexgrid-full", "flexgrid", "127.0.0.1",
                               server.port)
        mgr._devices[dev.device_id] = dev
        assert mgr.subscribe(dev.device_id) is False
        assert dev.subscribed is False
        assert "rejected" in dev.sub_error
    finally:
        server.stop()


# ---- probe / get_info -----------------------------------------------------

def test_probe_get_info(server, mgr):
    dev = mgr.probe("127.0.0.1", server.port)
    assert dev is not None
    assert dev.device_id == "flexgrid-test01"
    assert dev.device_type == "flexgrid"
    assert dev.source == "probe"
    assert dev.matrix == [15, 4]


def test_probe_unreachable_returns_none(mgr):
    # Nothing listening on this port.
    assert mgr.probe("127.0.0.1", 1) is None


# ---- auto-subscribe path --------------------------------------------------

def test_auto_subscribe_on_announce(server, tmp_path):
    m = DiscoveryManager(pc_host="127.0.0.1", udp_port=3141,
                         cache_path=str(tmp_path / "c.json"),
                         auto_subscribe=True)
    try:
        # Announce points cmd at the fake server's port.
        ann = _announce(device_id="flexgrid-test01", cmd=server.port)
        m.on_announce(ann, "127.0.0.1")
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if ("127.0.0.1", 3141) in server.subscribers:
                break
            time.sleep(0.05)
        assert ("127.0.0.1", 3141) in server.subscribers
    finally:
        m.stop()

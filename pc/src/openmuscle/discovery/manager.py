"""Native V4 discovery + subscribe manager for the PC hub.

Replaces the interim pc/bridge_subscriber.py. Discovers OpenMuscle V4 sources
and keeps a TCP subscription (with a 1 Hz heartbeat) to each, so the existing
UDP 3141 listener receives their sensor frames with no change to the parse or
state pipeline.

Three discovery paths, because no single one is sufficient:

  1. Passive beacons. V4 devices broadcast an `announce` to 255.255.255.255:3141
     at ~1 Hz WHILE THEY HAVE NO SUBSCRIBER. The moment any hub subscribes, the
     beacon goes silent and resumes only when the subscriber list empties
     (confirmed by the phone team). So passive listening alone misses any device
     another hub (or our own prior run) already holds.
  2. Cache. Every device we have ever seen is persisted (id -> ip, cmd_port,
     device_type). On startup we actively re-probe cached devices with the
     `get_info` command, recovering them even when their beacon is silent.
  3. Manual probe. Probe an explicit ip:cmd_port (lab setup / UI "add device").

Discovered + reachable devices are auto-subscribed when auto_subscribe is set.
The contract this implements is documented in FlexGridV4-Firmware/lib/
{discovery,commands,subscribers,network_manager}.py and confirmed on the
coordination board (vrpc #0008).
"""

import json
import logging
import os
import socket
import threading
import time

logger = logging.getLogger(__name__)

DEFAULT_UDP_PORT = 3141          # where we ask sources to unicast sensor frames
# Announce/discovery beacon port. PROTOCOL.md v1.0 (frozen 2026-06-23) splits the
# ports: beacons move to 3140, and 3141 becomes data-only. We bind a dedicated
# listener here; the shared-3141 type=="announce" tap remains a fallback only.
DEFAULT_ANNOUNCE_PORT = 3140
DEFAULT_CMD_PORTS = (8001, 8002)  # flexgrid cmd, lask5 cmd (probe order)
HEARTBEAT_S = 1.0                # firmware drops a subscriber after ~5 s silence
TCP_TIMEOUT_S = 3.0
RESUBSCRIBE_BACKOFF_S = 2.0      # wait between failed re-subscribe attempts
HUB_ID = "pc-native-discovery"


def default_lan_ip(test_target=("10.0.0.1", 80)):
    """Best-effort local LAN IP (the address sources should unicast to).

    Opens a throwaway UDP socket toward the gateway and reads the chosen
    source address; no packet is actually sent. Falls back to hostname
    resolution, then loopback.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(test_target)
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def default_cache_path():
    """~/.openmuscle/discovered_devices.json (override via DiscoveryManager)."""
    return os.path.join(os.path.expanduser("~"), ".openmuscle",
                        "discovered_devices.json")


class DiscoveredDevice:
    """A source we have discovered and may be subscribed to.

    Plain class (not a dataclass) so it stays import-light and easy to mutate
    under the manager lock.
    """

    __slots__ = ("device_id", "device_type", "ip", "cmd_port", "sensor_port",
                 "fw", "caps", "matrix", "source", "last_seen",
                 "subscribed", "sub_error")

    def __init__(self, device_id, device_type, ip, cmd_port,
                 sensor_port=DEFAULT_UDP_PORT, fw="", caps=None, matrix=None,
                 source="beacon", last_seen=0.0):
        self.device_id = device_id
        self.device_type = device_type
        self.ip = ip
        self.cmd_port = int(cmd_port)
        self.sensor_port = int(sensor_port)
        self.fw = fw
        self.caps = list(caps or [])
        self.matrix = list(matrix or [])
        self.source = source
        self.last_seen = last_seen
        self.subscribed = False
        self.sub_error = ""

    def to_cache(self):
        """Minimal persisted form (enough to re-probe next run)."""
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "ip": self.ip,
            "cmd_port": self.cmd_port,
            "sensor_port": self.sensor_port,
        }

    def to_snapshot(self, now=None):
        now = now if now is not None else time.time()
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "ip": self.ip,
            "cmd_port": self.cmd_port,
            "sensor_port": self.sensor_port,
            "fw": self.fw,
            "caps": list(self.caps),
            "matrix": list(self.matrix),
            "source": self.source,
            "age_s": round(now - self.last_seen, 2) if self.last_seen else None,
            "subscribed": self.subscribed,
            "sub_error": self.sub_error,
        }


def _send_cmd(sock, msg_id, verb, **fields):
    """Encode + send one newline-delimited command, return the parsed ack dict.

    Raises on socket error or malformed ack so callers can react (re-subscribe).
    """
    msg = {"v": "1.0", "type": "cmd", "msg_id": msg_id,
           "data": dict({"verb": verb}, **fields)}
    sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
    raw = sock.recv(4096)
    if not raw:
        raise ConnectionError("command channel closed (empty read)")
    # Firmware sends one ack JSON object per line; take the first line.
    line = raw.split(b"\n", 1)[0].strip()
    return json.loads(line.decode("utf-8"))


class _SubscriptionKeeper:
    """Owns one device's TCP command channel: subscribe, then heartbeat at
    1 Hz, auto-resubscribing on failure. Sensor frames arrive out-of-band on
    the manager's UDP port; this object never touches them.
    """

    def __init__(self, device, pc_host, udp_port, on_state):
        self.device = device
        self.pc_host = pc_host
        self.udp_port = udp_port
        self.on_state = on_state          # callback(device) on any state change
        self._stop = threading.Event()
        self._tcp = None
        self._thread = None
        self._msg_id = 1

    def start(self):
        """Subscribe once synchronously; on success spawn the heartbeat thread.
        Returns True if the device accepted the subscription."""
        if not self._connect_and_subscribe():
            return False
        self._thread = threading.Thread(
            target=self._loop, name="om-keeper-" + self.device.device_id,
            daemon=True)
        self._thread.start()
        return True

    def _connect_and_subscribe(self):
        try:
            tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp.settimeout(TCP_TIMEOUT_S)
            tcp.connect((self.device.ip, self.device.cmd_port))
            ack = _send_cmd(tcp, self._next_id(), "subscribe",
                            host=self.pc_host, port=self.udp_port,
                            transport="wifi", hub_id=HUB_ID)
            data = (ack or {}).get("data") or {}
            if not data.get("accepted"):
                tcp.close()
                self.device.subscribed = False
                self.device.sub_error = "rejected (list full?)"
                self._emit()
                return False
            self._tcp = tcp
            self.device.subscribed = True
            self.device.sub_error = ""
            self._emit()
            return True
        except Exception as e:
            self.device.subscribed = False
            self.device.sub_error = str(e)
            self._emit()
            return False

    def _loop(self):
        while not self._stop.is_set():
            if self._stop.wait(HEARTBEAT_S):
                break
            try:
                _send_cmd(self._tcp, self._next_id(), "heartbeat",
                          host=self.pc_host, port=self.udp_port,
                          transport="wifi")
            except Exception as e:
                # Connection dropped (device reboot, Wi-Fi blip). Try to
                # re-establish; back off so we don't spin on a dead host.
                self.device.subscribed = False
                self.device.sub_error = "heartbeat failed: {}".format(e)
                self._emit()
                self._safe_close()
                if self._stop.wait(RESUBSCRIBE_BACKOFF_S):
                    break
                self._connect_and_subscribe()

    def stop(self):
        self._stop.set()
        if self._tcp is not None:
            try:
                _send_cmd(self._tcp, 999, "unsubscribe",
                          host=self.pc_host, port=self.udp_port,
                          transport="wifi")
            except Exception:
                pass
        self._safe_close()
        self.device.subscribed = False
        self._emit()

    def _safe_close(self):
        if self._tcp is not None:
            try:
                self._tcp.close()
            except Exception:
                pass
            self._tcp = None

    def _next_id(self):
        self._msg_id += 1
        return self._msg_id

    def _emit(self):
        if self.on_state:
            try:
                self.on_state(self.device)
            except Exception:
                pass


class DiscoveryManager:
    """Tracks discovered V4 sources and keeps subscriptions to them.

    Thread model: on_announce() runs on the UDP listener thread; probe() and
    recover_cache() run on their own short-lived threads; snapshot() runs on
    the web request thread. A single lock guards the device + keeper maps.
    """

    def __init__(self, pc_host=None, udp_port=DEFAULT_UDP_PORT,
                 announce_port=DEFAULT_ANNOUNCE_PORT,
                 cache_path=None, auto_subscribe=True):
        self.pc_host = pc_host or default_lan_ip()
        self.udp_port = udp_port              # data frames land here (3141)
        self.announce_port = announce_port    # dedicated beacon port (3140)
        self.cache_path = cache_path if cache_path is not None else default_cache_path()
        self.auto_subscribe = auto_subscribe
        self._devices = {}     # device_id -> DiscoveredDevice
        self._keepers = {}     # device_id -> _SubscriptionKeeper
        self._lock = threading.Lock()
        # Dedicated announce-beacon listener (started in start()).
        self._announce_thread = None
        self._announce_stop = threading.Event()
        self._announce_started = False

    # ---- discovery inputs -------------------------------------------------

    def on_announce(self, obj, src_ip):
        """Handle one announce beacon. `obj` is the decoded announce dict;
        `src_ip` is the datagram source (the announce never carries its own IP).
        """
        try:
            device_id = obj["id"]
        except (KeyError, TypeError):
            return None
        services = obj.get("services") or {}
        cmd_port = int(services.get("cmd", DEFAULT_CMD_PORTS[0]))
        sensor_port = int(services.get("sensor", self.udp_port))
        dev = DiscoveredDevice(
            device_id=device_id,
            device_type=obj.get("dev", "unknown"),
            ip=src_ip,
            cmd_port=cmd_port,
            sensor_port=sensor_port,
            fw=obj.get("fw", ""),
            caps=obj.get("caps"),
            matrix=obj.get("matrix"),
            source="beacon",
            last_seen=time.time(),
        )
        return self._upsert(dev)

    def probe(self, ip, cmd_port=None):
        """Actively query ip:cmd_port (or try the default ports) with get_info.
        Returns the DiscoveredDevice on success, or None. Used for cache
        recovery and manual add."""
        ports = [int(cmd_port)] if cmd_port else list(DEFAULT_CMD_PORTS)
        for port in ports:
            try:
                tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                tcp.settimeout(TCP_TIMEOUT_S)
                tcp.connect((ip, port))
                ack = _send_cmd(tcp, 1, "get_info")
                tcp.close()
            except Exception:
                continue
            data = (ack or {}).get("data") or {}
            if not data.get("id"):
                continue
            dev = DiscoveredDevice(
                device_id=data["id"],
                device_type=data.get("dev", "unknown"),
                ip=ip,
                cmd_port=port,
                sensor_port=self.udp_port,
                fw=data.get("fw", ""),
                caps=data.get("caps"),
                matrix=data.get("matrix"),
                source="probe",
                last_seen=time.time(),
            )
            return self._upsert(dev)
        return None

    def recover_cache(self, background=True):
        """Re-probe every cached device so we recover ones whose beacon is
        silent (held by another hub). Non-blocking by default."""
        cached = self._load_cache()
        if not cached:
            return

        def _work():
            for entry in cached:
                ip = entry.get("ip")
                port = entry.get("cmd_port")
                if ip:
                    self.probe(ip, port)

        if background:
            threading.Thread(target=_work, name="om-cache-recover",
                             daemon=True).start()
        else:
            _work()

    # ---- lifecycle --------------------------------------------------------

    def start(self):
        """Start the dedicated announce-beacon listener and recover cached
        devices. Idempotent. The dedicated port (3140 per PROTOCOL.md v1.0) is
        the primary discovery path; the shared-3141 type-tap (wired via the data
        listener's announce_handler) stays a fallback only."""
        if not self._announce_started:
            self._announce_started = True
            self._announce_stop.clear()
            self._announce_thread = threading.Thread(
                target=self._announce_listen, name="om-announce-listener",
                daemon=True)
            self._announce_thread.start()
        self.recover_cache()

    def _announce_listen(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(1.0)
            sock.bind(("0.0.0.0", self.announce_port))
        except OSError as e:
            # Port busy / unavailable: fall back to the shared-3141 type-tap.
            logger.warning(
                "discovery: announce listener could not bind UDP %d (%s); "
                "relying on the 3141 fallback tap", self.announce_port, e)
            self._announce_started = False
            return
        logger.info("discovery: announce listener on UDP %d", self.announce_port)
        while not self._announce_stop.is_set():
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                obj = json.loads(data.decode("utf-8", "replace"))
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get("type") == "announce":
                try:
                    self.on_announce(obj, addr[0])
                except Exception as e:
                    logger.warning("discovery: on_announce failed: %s", e)
        sock.close()

    # ---- subscription control --------------------------------------------

    def subscribe(self, device_id):
        """Start (or restart) a subscription keeper for a known device."""
        with self._lock:
            dev = self._devices.get(device_id)
            if dev is None:
                return False
            existing = self._keepers.get(device_id)
            if existing is not None:
                return dev.subscribed
            keeper = _SubscriptionKeeper(dev, self.pc_host, self.udp_port,
                                         self._on_keeper_state)
            self._keepers[device_id] = keeper
        ok = keeper.start()
        if not ok:
            with self._lock:
                self._keepers.pop(device_id, None)
        return ok

    def unsubscribe(self, device_id):
        with self._lock:
            keeper = self._keepers.pop(device_id, None)
        if keeper is not None:
            keeper.stop()
            return True
        return False

    def snapshot(self):
        """List of device dicts for the web UI / API."""
        now = time.time()
        with self._lock:
            return [d.to_snapshot(now) for d in self._devices.values()]

    def stop(self):
        self._announce_stop.set()
        self._announce_started = False
        with self._lock:
            keepers = list(self._keepers.values())
            self._keepers.clear()
        for k in keepers:
            k.stop()

    # ---- internals --------------------------------------------------------

    def _upsert(self, dev):
        """Merge a freshly discovered device into the table, persist the cache,
        and auto-subscribe if enabled and not already subscribed."""
        with self._lock:
            existing = self._devices.get(dev.device_id)
            if existing is None:
                self._devices[dev.device_id] = dev
                target = dev
            else:
                # Refresh address/metadata + last_seen; keep subscription state.
                existing.ip = dev.ip
                existing.cmd_port = dev.cmd_port
                existing.sensor_port = dev.sensor_port
                existing.device_type = dev.device_type
                existing.fw = dev.fw or existing.fw
                existing.caps = dev.caps or existing.caps
                existing.matrix = dev.matrix or existing.matrix
                existing.source = dev.source
                existing.last_seen = dev.last_seen
                target = existing
            already_subscribed = target.device_id in self._keepers
        self._save_cache()
        if self.auto_subscribe and not already_subscribed:
            self.subscribe(target.device_id)
        return target

    def _on_keeper_state(self, device):
        # Keeper mutated device.subscribed/sub_error in place; nothing else to
        # do today, but this is where UI push / logging would hook in.
        pass

    def _load_cache(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (FileNotFoundError, ValueError, OSError):
            return []

    def _save_cache(self):
        if not self.cache_path:
            return
        with self._lock:
            entries = [d.to_cache() for d in self._devices.values()]
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2)
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

"""Native V4 discovery + subscribe manager for the PC hub.

Replaces the interim pc/bridge_subscriber.py. Discovers OpenMuscle V4 sources
and keeps a TCP subscription (with a 1 Hz heartbeat) to each, so the existing
UDP 3141 listener receives their sensor frames with no change to the parse or
state pipeline.

Four discovery paths, because no single one is sufficient:

  1. Passive beacons. V4 devices broadcast an `announce` to 255.255.255.255:3141
     at ~1 Hz WHILE THEY HAVE NO SUBSCRIBER. The moment any hub subscribes, the
     beacon goes silent and resumes only when the subscriber list empties
     (confirmed by the phone team). So passive listening alone misses any device
     another hub (or our own prior run) already holds.
  2. Cache. Every device we have ever seen is persisted (id -> ip, cmd_port,
     device_type). On startup we actively re-probe cached devices with the
     `get_info` command, recovering them even when their beacon is silent.
  3. Manual probe. Probe an explicit ip:cmd_port (lab setup / UI "add device").
  4. Active discover. Broadcast {"verb":"discover"} to the announce port (PROTOCOL
     5.5); every device answers at once with an announce, subscribed or not. Sent
     at startup, whenever this computer's address changes, on a timer, and on
     demand (send_discover), so a band is found without waiting for a beacon.

Discovered + reachable devices are auto-subscribed when auto_subscribe is set.
The contract this implements is documented in FlexGridV4-Firmware/lib/
{discovery,commands,subscribers,network_manager}.py and confirmed on the
coordination board.
"""

import json
import logging
import os
import socket
import threading
import time

from openmuscle import netutil
from openmuscle.netutil import default_lan_ip, local_ip_for

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
DISCOVER_INTERVAL_S = 10.0       # active discover probe period while running
NET_POLL_S = 5.0                 # how often to notice a changed local address
DISCOVER_WAIT_S = 0.6            # how long a probe waits for replies
MAX_SCAN_HOSTS = 1024            # hard cap for "Search my network"
DISCOVER_PROBE = {"v": "1.0", "type": "cmd", "data": {"verb": "discover"}}
ROLES = ("left", "right", "labeler")
# Device types that keep their role only on this computer: the LASK5 labeler has
# no set_role verb, so the Studio's tag is hub-local for it (no push attempted).
LOCAL_ROLE_TYPES = ("lask5",)
# After the Studio pushes a role, status frames already in flight still carry
# the old one; ignore the band's reports for this long so they cannot undo it.
ROLE_HOLDOFF_S = 3.0


def default_cache_path():
    """<data root>/devices.json (override via DiscoveryManager(cache_path=...)).

    The first call under a data root with no such file copies the older
    ~/.openmuscle/discovered_devices.json there once."""
    from openmuscle import paths
    return str(paths.migrate_devices_cache())


def subnet_base(ip):
    """The /24 prefix of a dotted-quad IP ("10.0.0.23" -> "10.0.0"), or None if
    it is not a usable IPv4 (loopback / malformed)."""
    if not ip or ip.startswith("127."):
        return None
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    try:
        if not all(0 <= int(p) <= 255 for p in parts):
            return None
    except ValueError:
        return None
    return ".".join(parts[:3])


class DiscoveredDevice:
    """A source we have discovered and may be subscribed to.

    Plain class (not a dataclass) so it stays import-light and easy to mutate
    under the manager lock.
    """

    __slots__ = ("device_id", "device_type", "ip", "cmd_port", "sensor_port",
                 "fw", "caps", "matrix", "source", "last_seen",
                 "subscribed", "sub_error", "role", "imu",
                 "band_role", "role_note", "role_pushed_at")

    def __init__(self, device_id, device_type, ip, cmd_port,
                 sensor_port=DEFAULT_UDP_PORT, fw="", caps=None, matrix=None,
                 source="beacon", last_seen=0.0, role="", imu=None):
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
        # Hub-assigned role for capture (left / right / labeler), or "" if
        # untagged. Persisted per device_id so it survives restarts + churn.
        self.role = role
        # Per-chip IMU scale the firmware advertises on announce/get_info
        # as {chip, gyro_dps_per_lsb, accel_g_per_lsb}. Captured into
        # the recording meta so the trainer can normalize the RAW counts the CSV
        # stores (Option A). {} until the device advertises it.
        self.imu = dict(imu or {})
        # The role the band itself last reported (get_info reply, status frame
        # meta.role): None until it has said anything, "" when it has no role.
        # role_note is a sentence for the user when the band could not be told
        # the role picked in the Studio ("" when in step).
        self.band_role = None
        self.role_note = ""
        self.role_pushed_at = 0.0

    def to_cache(self):
        """Persisted form. PROTOCOL.md v1.0 S5.3 requires the cache hold at
        minimum the last known IP, cmd port, and last-contact timestamp."""
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "ip": self.ip,
            "cmd_port": self.cmd_port,
            "sensor_port": self.sensor_port,
            "last_contact": self.last_seen,
            "role": self.role,
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
            "role": self.role,
            "band_role": self.band_role,
            "role_note": self.role_note,
            "imu": dict(self.imu),
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


def _role_error_text(exc):
    """A short reason a role push failed, in words a person can act on."""
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "it did not answer in time"
    if isinstance(exc, OSError):
        return "it could not be reached"
    return str(exc) or exc.__class__.__name__


class _SubscriptionKeeper:
    """Owns one device's TCP command channel: subscribe, then heartbeat at
    1 Hz, auto-resubscribing on failure. Sensor frames arrive out-of-band on
    the manager's UDP port; this object never touches them.

    pc_host is the address the device should send frames to. Pass a string for a
    fixed address, or a callable(device_ip) -> str to work it out per device and
    again at every subscribe, resubscribe and heartbeat (a laptop that moves
    between networks keeps telling the band the address that now reaches it).
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
        self._host = ""                   # last address we told the device

    def _resolve_host(self):
        ph = self.pc_host
        host = ph(self.device.ip) if callable(ph) else ph
        return host or ""

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
            host = self._resolve_host()
            if not host:
                raise OSError("this computer has no address on the same network "
                              "as the device (%s)" % self.device.ip)
            tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp.settimeout(TCP_TIMEOUT_S)
            tcp.connect((self.device.ip, self.device.cmd_port))
            ack = _send_cmd(tcp, self._next_id(), "subscribe",
                            host=host, port=self.udp_port,
                            transport="wifi", hub_id=HUB_ID)
            data = (ack or {}).get("data") or {}
            if not data.get("accepted"):
                tcp.close()
                self.device.subscribed = False
                self.device.sub_error = "rejected (list full?)"
                self._emit()
                return False
            self._tcp = tcp
            self._host = host
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
                host = self._resolve_host()
                if not host:
                    raise OSError("no address on the device's network")
                if host != self._host:
                    logger.info("discovery: address toward %s changed %s -> %s",
                                self.device.device_id, self._host, host)
                    self._host = host
                _send_cmd(self._tcp, self._next_id(), "heartbeat",
                          host=host, port=self.udp_port,
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
                          host=self._host, port=self.udp_port,
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
                 cache_path=None, auto_subscribe=True, on_event=None,
                 discover_targets=None, discover_interval_s=DISCOVER_INTERVAL_S,
                 net_poll_s=NET_POLL_S):
        # Optional callback(kind, device) fired from _upsert when a source is
        # seen for the first time this run (kind == "found"). The web server
        # uses it to print a plain "Found FlexGrid band at <ip>" console line.
        self.on_event = on_event
        # An explicit pc_host pins the address told to every device. Left unset
        # (the normal case) it is worked out per device with a UDP connect() to
        # that device's own IP, so a band on 192.168.4.x hears a 192.168.4.x
        # address and one on the home router hears the home address. self.pc_host
        # is then just the main address, refreshed when this computer moves.
        self._pc_host_pinned = bool(pc_host)
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
        # Listener health: None before start(), True once UDP 3140 is bound,
        # False when the bind failed (then only the 3141 fallback tap and the
        # active discover probe can find devices). announce_error is a sentence.
        self.listener_ok = None
        self.announce_error = ""
        # Beacon / probe health counters (read by diagnose and the status view).
        self.last_announce_time = 0.0
        self.announce_total = 0
        self._announce_rate = netutil.RateWindow()
        self.last_discover_time = 0.0
        self.last_discover_found = 0
        self._discover_targets = discover_targets   # None = broadcast addresses
        self.discover_interval_s = discover_interval_s
        self.net_poll_s = net_poll_s
        self._watch_thread = None
        self._pc_by_device = {}    # device_id -> address last told to that device

    def pc_host_for(self, device_ip):
        """The address of this computer that `device_ip` can reach.

        Never loopback for a device that is not itself on loopback: a band on
        another machine cannot send frames to 127.x, so "" (no usable address)
        is returned instead and the subscribe fails with a readable reason.
        """
        if self._pc_host_pinned:
            return self.pc_host
        host = local_ip_for(device_ip)
        if host and host.startswith("0."):
            host = None
        on_loopback = bool(device_ip) and device_ip.startswith("127.")
        if host and (on_loopback or not host.startswith("127.")):
            return host
        if on_loopback:
            return "127.0.0.1"
        fallback = self.pc_host
        return fallback if fallback and not fallback.startswith("127.") else ""

    def _host_for_device(self, device):
        host = self.pc_host_for(device.ip)
        if host:
            self._pc_by_device[device.device_id] = host
        return host

    def _pc_host_resolver(self, dev):
        """callable(device_ip) for a keeper: the address toward that device now."""
        def _resolve(_ip=None):
            return self._host_for_device(dev)
        return _resolve

    # ---- discovery inputs -------------------------------------------------

    def on_announce(self, obj, src_ip):
        """Handle one announce beacon. `obj` is the decoded announce dict;
        `src_ip` is the datagram source (the announce never carries its own IP).
        """
        try:
            device_id = obj["id"]
        except (KeyError, TypeError):
            return None
        self.last_announce_time = time.time()
        self.announce_total += 1
        self._announce_rate.add()
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
            imu=obj.get("imu"),
        )
        return self._upsert(dev)

    def probe(self, ip, cmd_port=None, timeout=None, role=""):
        """Actively query ip:cmd_port (or try the default ports) with get_info.
        Returns the DiscoveredDevice on success, or None. Used for cache
        recovery, manual add, and the subnet scan (which passes a short timeout
        so dead hosts fail fast). `role` is the cached local role a recovered
        device starts with; the role the band reports in its reply is then
        adopted by the usual rule (see observe_role)."""
        ports = [int(cmd_port)] if cmd_port else list(DEFAULT_CMD_PORTS)
        to = TCP_TIMEOUT_S if timeout is None else timeout
        for port in ports:
            try:
                tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                tcp.settimeout(to)
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
                role=role if role in ROLES else "",
                imu=data.get("imu"),
            )
            target = self._upsert(dev)
            self.observe_role(target.device_id, data.get("role") or "")
            return target
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
                role = entry.get("role", "")
                if ip:
                    # The cached role seeds the recovered device, so what the
                    # band reports does not replace a tag chosen here.
                    dev = self.probe(ip, port, role=role)
                    if dev is not None and role in ROLES:
                        with self._lock:
                            if not dev.role:
                                dev.role = role

        if background:
            threading.Thread(target=_work, name="om-cache-recover",
                             daemon=True).start()
        else:
            _work()

    def scan_subnet(self, base=None, start=1, end=254, timeout=0.5,
                    max_workers=32, background=True, prefix=24,
                    max_hosts=MAX_SCAN_HOSTS):
        """Search my network: probe the hosts around this computer with
        get_info to find V4 devices.

        The cold-cache discovery path: a device subscribed by another hub
        suppresses its beacon, so a freshly started hub with no cache cannot see
        it passively. Scanning the subnet reaches it without a manual IP (P3
        exit: "a late-joining hub reaches a singly-subscribed device"). Found
        devices flow through probe() -> _upsert (cache + auto-subscribe), exactly
        like the other discovery paths. Non-blocking by default.

        prefix is the network size: 24 (default, 254 hosts) or 16 (a bigger
        home or office network, scanned outward from this computer's own /24).
        At most max_hosts addresses are tried, so a /16 never turns into a
        65,000-address sweep.

        Returns the worker thread when background, else the list of device_ids
        found this scan.
        """
        base = base or subnet_base(self.pc_host)
        if not base:
            logger.warning("discovery: subnet scan skipped, no usable LAN IP "
                           "(pc_host=%s)", self.pc_host)
            return None
        lo, hi = max(1, int(start)), min(254, int(end))
        ips = []
        for blk in self._scan_blocks(base, int(prefix)):
            ips.extend("%s.%d" % (blk, h) for h in range(lo, hi + 1))
        ips = ips[:max(1, int(max_hosts))]

        def _scan_one(ip):
            if ip == self.pc_host:
                return None
            dev = self.probe(ip, timeout=timeout)
            return dev.device_id if dev is not None else None

        def _work():
            found = []
            try:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    for did in pool.map(_scan_one, ips):
                        if did:
                            found.append(did)
            except Exception as e:
                logger.warning("discovery: subnet scan failed: %s", e)
            logger.info("discovery: subnet scan of %d address(es) around %s.x "
                        "found %d device(s)", len(ips), base, len(found))
            return found

        if background:
            t = threading.Thread(target=_work, name="om-subnet-scan",
                                 daemon=True)
            t.start()
            return t
        return _work()

    @staticmethod
    def _scan_blocks(base, prefix):
        """The /24 blocks ("a.b.c") a scan covers: just this one for /24, the
        enclosing /16 ordered nearest-first for /16."""
        if prefix >= 24:
            return [base]
        a, b, c = base.split(".")
        mine = int(c)
        order = sorted(range(256), key=lambda n: (abs(n - mine), n))
        return ["%s.%s.%d" % (a, b, n) for n in order]

    # ---- active discover (PROTOCOL 5.5) -------------------------------------

    def broadcast_targets(self):
        """Addresses a discover probe is sent to: limited broadcast plus the
        broadcast address of each local network (assumed /24, the home-router
        norm; the limited broadcast covers the rest)."""
        if self._discover_targets is not None:
            return list(self._discover_targets)
        targets = ["255.255.255.255"]
        for ip in netutil.local_ipv4_addresses():
            b = ".".join(ip.split(".")[:3]) + ".255"
            if b not in targets:
                targets.append(b)
        return targets

    def send_discover(self, wait_s=DISCOVER_WAIT_S):
        """Ask every device on the network to announce itself right now.

        Broadcasts {"verb":"discover"} to the announce port. Each device
        answers with an immediate announce, broadcast AND unicast to this
        probe's own socket, which is read here, so a device is found within a
        round trip without waiting for its ~1 Hz beacon (and a subscribed
        device, whose beacon is silent, answers too). Returns the device_ids
        that answered. Safe to call from any thread.
        """
        payload = json.dumps(DISCOVER_PROBE).encode("utf-8")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        found = []
        try:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass
            sock.bind(("0.0.0.0", 0))
            sent = 0
            for target in self.broadcast_targets():
                try:
                    sock.sendto(payload, (target, self.announce_port))
                    sent += 1
                except OSError:
                    continue
            self.last_discover_time = time.time()
            if not sent:
                return found
            deadline = time.time() + max(0.05, wait_s)
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    data, addr = sock.recvfrom(8192)
                except (socket.timeout, OSError):
                    break
                try:
                    obj = json.loads(data.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("type") == "announce":
                    dev = self.on_announce(obj, addr[0])
                    if dev is not None and dev.device_id not in found:
                        found.append(dev.device_id)
                    # Gather the other devices' replies, but do not sit out the
                    # whole window once the first one is in.
                    deadline = min(deadline, time.time() + 0.25)
        finally:
            sock.close()
        self.last_discover_found = len(found)
        return found

    # ---- lifecycle --------------------------------------------------------

    def start(self):
        """Start the dedicated announce-beacon listener, the network watcher and
        recover cached devices. Idempotent. The dedicated port (3140 per
        PROTOCOL.md v1.0) is the primary discovery path; the shared-3141
        type-tap (wired via the data listener's announce_handler) stays a
        fallback only."""
        if not self._announce_started:
            self._announce_started = True
            self._announce_stop.clear()
            # Bind in the caller's thread so a busy port is known (listener_ok)
            # by the time start() returns.
            sock = self._bind_announce_socket()
            if sock is not None:
                self._announce_thread = threading.Thread(
                    target=self._announce_listen, args=(sock,),
                    name="om-announce-listener", daemon=True)
                self._announce_thread.start()
            if self._watch_thread is None or not self._watch_thread.is_alive():
                self._watch_thread = threading.Thread(
                    target=self._watch_network, name="om-net-watch", daemon=True)
                self._watch_thread.start()
        self.recover_cache()

    def _bind_announce_socket(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Exclusive: a second OpenMuscle window must not share the beacon
            # port (both would subscribe to every band).
            netutil.make_exclusive(sock)
            sock.settimeout(1.0)
            sock.bind(("0.0.0.0", self.announce_port))
        except OSError as e:
            # Port busy / unavailable: fall back to the shared-3141 type-tap.
            self.listener_ok = False
            self.announce_error = netutil.bind_error_text(self.announce_port, "UDP", e)
            logger.warning(
                "discovery: announce listener could not bind UDP %d (%s); "
                "relying on the 3141 fallback tap and the discover probe",
                self.announce_port, e)
            return None
        self.listener_ok = True
        self.announce_error = ""
        logger.info("discovery: announce listener on UDP %d", self.announce_port)
        return sock

    def _announce_listen(self, sock):
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

    def _watch_network(self):
        """Daemon thread: send a discover probe at startup, every
        discover_interval_s, and the moment this computer's address changes
        (laptop moved to another Wi-Fi, hotspot came up)."""
        next_probe = 0.0
        while not self._announce_stop.is_set():
            changed = False
            if not self._pc_host_pinned:
                try:
                    ip = default_lan_ip()
                except Exception:
                    ip = self.pc_host
                if ip and ip != self.pc_host:
                    logger.info("discovery: this computer's address changed %s -> %s",
                                self.pc_host, ip)
                    self.pc_host = ip
                    changed = True
            if changed or time.time() >= next_probe:
                try:
                    self.send_discover()
                except Exception as e:
                    logger.warning("discovery: discover probe failed: %s", e)
                next_probe = time.time() + self.discover_interval_s
            if self._announce_stop.wait(self.net_poll_s):
                break

    def status(self, data_listener=None, full=True):
        """Health view for the UI: this computer's address toward the bands,
        listener health and beacon counters. `data_listener` is the UDP 3141
        UDPListener (owned by the web state), included so one object answers
        "is anything listening". full=False skips the adapter-name lookup."""
        now = time.time()
        data_ok = None
        data_error = ""
        if data_listener is not None:
            data_ok = data_listener.bound
            data_error = data_listener.bind_error
        return {
            "pc_host": self.pc_host,
            "pc_host_pinned": self._pc_host_pinned,
            "pc_host_by_device": dict(self._pc_by_device),
            "interface": (netutil.adapter_name_for_ip(self.pc_host)
                          if full and netutil.is_usable_ipv4(self.pc_host) else None),
            "listener_ok": {"announce": self.listener_ok, "data": data_ok},
            "listener_errors": {"announce": self.announce_error, "data": data_error},
            "ports": {"announce": self.announce_port, "data": self.udp_port},
            "last_announce_s": (round(now - self.last_announce_time, 2)
                                if self.last_announce_time else None),
            "announces_per_min": self._announce_rate.per_minute(now),
            "announces_total": self.announce_total,
            "last_discover_s": (round(now - self.last_discover_time, 2)
                                if self.last_discover_time else None),
            "last_discover_found": self.last_discover_found,
        }

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
            keeper = _SubscriptionKeeper(dev, self._pc_host_resolver(dev),
                                         self.udp_port, self._on_keeper_state)
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

    def set_role(self, device_id, role):
        """Assign a capture role to a known device, persisted per device_id.

        role is one of the lowercase wire tokens left/right/labeler, or "" to
        clear the tag. Returns True if the device is known. Hub-local only: it
        never talks to the band (use assign_role for that). Simulators and the
        LASK5 labeler keep their role this way.
        """
        role = (role or "").strip().lower()
        if role not in ("",) + ROLES:
            return False
        with self._lock:
            dev = self._devices.get(device_id)
            if dev is None:
                return False
            dev.role = role
        self._save_cache()
        return True

    def assign_role(self, device_id, role):
        """The Studio's role change: tag the device here AND tell the band.

        The local tag is always set first, so the Studio never loses the user's
        choice. Then the band's own `set_role` verb is sent over its command
        port (it persists the role, colours its idle LED and shows it on its
        status screen). Best effort: when the band cannot be told, the local
        role stays and the result carries a sentence saying so.

        Returns {"ok", "role", "band", "message"}. ok is False for an unknown
        device or an invalid role; band is "updated" (band acked), "local"
        (a device type that has no role verb, nothing to send) or "failed".
        Blocks for up to TCP_TIMEOUT_S, so call it off the event loop.
        """
        role = (role or "").strip().lower()
        if not self.set_role(device_id, role):
            return {"ok": False, "role": role, "band": "failed", "message": ""}
        with self._lock:
            dev = self._devices.get(device_id)
            if dev is None:
                return {"ok": False, "role": role, "band": "failed", "message": ""}
            ip, port, dtype = dev.ip, dev.cmd_port, dev.device_type
            if dtype in LOCAL_ROLE_TYPES:
                dev.role_note = ""
                return {"ok": True, "role": role, "band": "local", "message": ""}
            dev.role_pushed_at = time.time()
        try:
            self._push_role(ip, port, role)
        except Exception as e:
            message = ("The band itself was not updated ({}). The Studio still "
                       "uses {} for it, but the band's own light and screen "
                       "will not match.".format(_role_error_text(e),
                                                role or "no role"))
            with self._lock:
                dev = self._devices.get(device_id)
                if dev is not None:
                    dev.role_note = message
                    dev.role_pushed_at = 0.0
            logger.warning("discovery: set_role to %s failed: %s", device_id, e)
            return {"ok": True, "role": role, "band": "failed", "message": message}
        with self._lock:
            dev = self._devices.get(device_id)
            if dev is not None:
                dev.band_role = role
                dev.role_note = ""
                dev.role_pushed_at = time.time()
        return {"ok": True, "role": role, "band": "updated", "message": ""}

    def _push_role(self, ip, cmd_port, role):
        """Send the band's set_role verb on a short-lived command connection and
        check that the ack echoes the role. Raises on any failure."""
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            tcp.settimeout(TCP_TIMEOUT_S)
            tcp.connect((ip, int(cmd_port)))
            ack = _send_cmd(tcp, 1, "set_role", role=role)
        finally:
            try:
                tcp.close()
            except Exception:
                pass
        ack = ack if isinstance(ack, dict) else {}
        data = ack.get("data") or {}
        if ack.get("status") not in (None, "ok"):
            raise RuntimeError(str(data.get("message") or ack.get("status")))
        if "role" not in data:
            raise RuntimeError("the band's reply did not include a role")
        if (data.get("role") or "") != role:
            raise RuntimeError("the band answered with role '{}'".format(
                data.get("role") or ""))

    def observe_role(self, device_id, role):
        """The band reported its own role (get_info reply, status frame
        meta.role). Adopt it as the Studio's tag when that is what the user
        would expect. Returns True if the local role changed.

        Rules: an empty or unknown report never clears a tag. The first report
        from a band is adopted only when the Studio has no tag for it (a tag
        chosen here, e.g. kept from the last run, wins). After that, a report
        that differs from the band's previous one means the band was re-tagged
        (sticker tool, setup page), so it is adopted. A report equal to the
        previous one changes nothing, so a role cleared or changed in the
        Studio whose push failed is not undone by the band's old value.
        Reports inside ROLE_HOLDOFF_S of a push are dropped (frames in flight).
        """
        role = (role or "").strip().lower() if isinstance(role, str) else None
        if role is None or role not in ("",) + ROLES:
            return False
        changed = False
        with self._lock:
            dev = self._devices.get(device_id)
            if dev is None:
                return False
            if time.time() - dev.role_pushed_at < ROLE_HOLDOFF_S:
                return False
            prev = dev.band_role
            dev.band_role = role
            if role and role != dev.role:
                if (prev is None and not dev.role) or (prev is not None and role != prev):
                    dev.role = role
                    dev.role_note = ""
                    changed = True
        if changed:
            logger.info("discovery: adopted role '%s' reported by %s", role, device_id)
            self._save_cache()
        return changed

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
                existing.imu = dev.imu or existing.imu
                existing.source = dev.source
                existing.last_seen = dev.last_seen
                target = existing
            already_subscribed = target.device_id in self._keepers
        if existing is None and self.on_event is not None:
            try:
                self.on_event("found", target)
            except Exception:
                pass
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

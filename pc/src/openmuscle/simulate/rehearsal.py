"""Hardware-free rehearsal kit: V4 bands + a finger labeler that really correlate.

`openmuscle simulate --device-type rehearsal` behaves like a small V4 kit on the
Wi-Fi: one or two 15x4 pressure bands (left / right) announcing themselves on the
discovery beacon port, answering the TCP command channel (subscribe, heartbeat,
get_info), and streaming v1.0 flexgrid frames with IMU and battery telemetry at
25 Hz. A finger labeler (LASK5 over UDP, or a webcam / Quest hand over the web
server's WebSocket) is driven by the SAME latent finger curls, so a capture
recorded from the rig is learnable: record, train and predict give a sensible
model with no hardware, and the --noise knob produces a weak dataset on demand.

The latent signal is quest_hand.finger_curls() gated by a rest / active schedule
(a few seconds at rest, then a stretch of free movement, repeating), the same
shape a guided recording protocol produces.

Layering:
  RehearsalConfig   validated settings (bands, labeler, noise, seed, rate)
  RehearsalRig      PURE generator: tick(t) -> the packets for time t. No
                    sockets, no clocks; the tests call it directly.
  RehearsalSimulator  the transports around a rig: UDP frames, discovery
                    beacons, a TCP command server per device, the WebSocket
                    label stream, and optional role tagging on the hub.
"""

import json
import math
import random
import socket
import socketserver
import threading
import time
from dataclasses import dataclass

from openmuscle.simulate.quest_hand import finger_curls, hand_frame
from openmuscle.simulate import net

# ---- V4 band geometry and ranges --------------------------------------------

BAND_COLS = 15            # wire matrix is [cols][rows]: 15 columns of 4 sensors
BAND_ROWS = 4
BAND_CELLS = BAND_COLS * BAND_ROWS      # 60 features per band
ADC_MAX = 4095
FW_VERSION = "sim-rehearsal-1.0"

# Per-chip IMU scale a V4 band advertises (icm42688 at +-2 g / +-2000 dps), so
# the hub records it into capture meta exactly as it would for hardware.
IMU_SCALE = {"chip": "icm42688", "gyro_dps_per_lsb": 0.0305,
             "accel_g_per_lsb": 0.000061}

# Noise multiplier presets for --noise. 1.0 is a clean, learnable kit; "high" buries
# the signal so a model trained on it should score near zero held out.
NOISE_PRESETS = {"low": 0.4, "default": 1.0, "high": 100.0}

LABELERS = ("lask5", "webcam", "quest")

# Rest / active schedule of the latent curls (seconds). Each period starts with
# a hold at rest, ramps into free movement, and ramps back out.
PERIOD_S = 10.0
REST_S = 2.5
RAMP_S = 0.5

# The pressure leads the finger motion slightly, as muscle tension does.
BAND_LEAD_S = 0.04


def parse_noise(value) -> float:
    """Noise knob: a preset name (low / default / high) or a non-negative number."""
    if isinstance(value, (int, float)):
        noise = float(value)
    else:
        text = str(value).strip().lower()
        if text in NOISE_PRESETS:
            return NOISE_PRESETS[text]
        try:
            noise = float(text)
        except ValueError:
            raise ValueError("noise must be low, default, high or a number, got %r"
                             % value)
    if not math.isfinite(noise) or noise < 0:
        raise ValueError("noise must be >= 0, got %r" % value)
    return noise


@dataclass
class RehearsalConfig:
    bands: int = 2                 # 1 (left) or 2 (left + right)
    labeler: str = "lask5"         # lask5 (UDP) | webcam | quest (WebSocket)
    noise: float = 1.0             # multiplier; see NOISE_PRESETS
    seed: int = 1138
    rate_hz: float = 25.0
    device_prefix: str = "flexgrid-sim"

    def __post_init__(self):
        if self.bands not in (1, 2):
            raise ValueError("bands must be 1 or 2, got %r" % (self.bands,))
        if self.labeler not in LABELERS:
            raise ValueError("labeler must be one of %s, got %r"
                             % (", ".join(LABELERS), self.labeler))
        self.noise = parse_noise(self.noise)
        if not self.rate_hz > 0:
            raise ValueError("rate_hz must be > 0")

    @property
    def sides(self) -> tuple:
        return ("left", "right")[:self.bands]


# ---- latent finger signal -----------------------------------------------------

def _smoothstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def activity(t: float) -> float:
    """Rest / active gate in [0, 1]: 0 while resting, 1 while moving."""
    phase = t % PERIOD_S
    if phase < REST_S:
        return 0.0
    if phase < REST_S + RAMP_S:
        return _smoothstep((phase - REST_S) / RAMP_S)
    if phase < PERIOD_S - RAMP_S:
        return 1.0
    return _smoothstep((PERIOD_S - phase) / RAMP_S)


def latent_curls(t: float) -> tuple:
    """The shared finger-curl signal (thumb..pinky, each in [0, 1]) at time t."""
    a = activity(t)
    return tuple(a * c for c in finger_curls(t))


# ---- the bands -----------------------------------------------------------------

# Where each finger's muscle belly sits across the 15 columns (thumb..pinky).
_FINGER_CENTERS = (1.5, 4.5, 7.5, 10.5, 13.0)


class _Band:
    """One V4 band: fixed per-cell mixing of the finger curls, a gain and offset
    per cell, and its own noise stream. Left and right are built from different
    seeds and mirror the finger layout, so a model trained on one band does not
    transfer to the other (as with real forearms)."""

    def __init__(self, device_id: str, side: str, seed: int):
        self.device_id = device_id
        self.side = side
        rng = random.Random(seed)
        self.rng = random.Random(seed + 1)
        centers = [c + rng.uniform(-0.6, 0.6) for c in _FINGER_CENTERS]
        if side == "right":
            centers = [(BAND_COLS - 1) - c for c in centers]
        sigma2 = 2 * 1.9 ** 2
        self.weights = []     # [col][row] -> list of 5 finger weights
        self.gain = []        # [col][row]
        self.offset = []      # [col][row]  ADC baseline with no pressure
        for col in range(BAND_COLS):
            w_col, g_col, o_col = [], [], []
            for row in range(BAND_ROWS):
                row_gain = 0.75 + 0.1 * row
                w = [math.exp(-((col - c) ** 2) / sigma2) * row_gain
                     * rng.uniform(0.8, 1.2) + rng.uniform(0.0, 0.08)
                     for c in centers]
                w_col.append(w)
                g_col.append(rng.uniform(0.75, 1.25))
                o_col.append(rng.uniform(180.0, 700.0))
            self.weights.append(w_col)
            self.gain.append(g_col)
            self.offset.append(o_col)
        # Slow common-mode drift (strap shift, skin temperature): per-band phases.
        self._drift = [(rng.uniform(0.03, 0.09), rng.uniform(0, 6.28))
                       for _ in range(3)]
        self._roll_phase = rng.uniform(0, 6.28)
        self._boot = rng.randint(60, 3600)     # uptime already on the clock
        self.frame_no = 0

    def matrix(self, curls, t: float, noise: float) -> list:
        """The [cols][rows] ADC matrix for these curls at time t."""
        drift = sum(math.sin(2 * math.pi * f * t + p) for f, p in self._drift)
        out = []
        for col in range(BAND_COLS):
            col_out = []
            for row in range(BAND_ROWS):
                x = self.gain[col][row] * sum(
                    w * c for w, c in zip(self.weights[col][row], curls))
                # Saturating response: pressure sensors flatten at high load.
                level = 1.0 - math.exp(-1.5 * x)
                v = (self.offset[col][row] + 2600.0 * level
                     + noise * (45.0 * self.rng.gauss(0, 1) + 22.0 * drift))
                col_out.append(max(0, min(ADC_MAX, int(round(v)))))
            out.append(col_out)
        return out

    def imu(self, t: float, noise: float) -> dict:
        """Raw IMU counts: gravity tilting slowly with the forearm roll, small
        gyro motion, and sensor noise. Scale is IMU_SCALE (announced)."""
        roll = 0.5 * math.sin(2 * math.pi * 0.05 * t + self._roll_phase)
        one_g = 1.0 / IMU_SCALE["accel_g_per_lsb"]
        accel = [one_g * math.sin(roll) * 0.6, one_g * 0.05,
                 one_g * math.cos(roll)]
        droll = 0.5 * 2 * math.pi * 0.05 * math.cos(
            2 * math.pi * 0.05 * t + self._roll_phase)       # rad/s
        gyro = [math.degrees(droll) / IMU_SCALE["gyro_dps_per_lsb"], 0.0, 0.0]
        jitter = 1.0 + 0.25 * noise
        return {
            "gyro": [int(g + self.rng.gauss(0, 12 * jitter)) for g in gyro],
            "accel": [int(a + self.rng.gauss(0, 25 * jitter)) for a in accel],
        }

    def meta(self, t: float, role: str = "") -> dict:
        """~1 Hz status telemetry (battery, uptime, memory, signal, and the
        band's own role: "" until something sets it, like the real band)."""
        vbat = max(3.3, 4.05 - 0.00004 * t) + self.rng.uniform(-0.01, 0.01)
        return {
            "vbat": round(vbat, 3),
            "pct": int(max(0, min(100, (vbat - 3.3) / 0.75 * 100))),
            "uptime_s": int(self._boot + t),
            "free_mem": 96000 + self.rng.randint(-1500, 1500),
            "rssi": -52 + self.rng.randint(-4, 4),
            "reset_cause_name": "power_on",
            "role": role,
        }


# ---- the labeler -----------------------------------------------------------------

# LASK5 pistons read 0..1 (extended..curled) in firmware; the joystick is a raw
# 12-bit ADC. Pistons follow index..pinky, the thumb rides the joystick.
_PISTON_FINGERS = (1, 2, 3, 4)


class _Lask5:
    def __init__(self, device_id: str, seed: int):
        self.device_id = device_id
        rng = random.Random(seed)
        self.rng = random.Random(seed + 1)
        # Slightly different calibration per piston, like real hardware.
        self.cal = [(rng.uniform(0.9, 1.05), rng.uniform(-0.03, 0.04))
                    for _ in _PISTON_FINGERS]
        self._boot = rng.randint(60, 3600)

    def values(self, curls) -> list:
        out = []
        for (gain, off), finger in zip(self.cal, _PISTON_FINGERS):
            v = gain * curls[finger] + off + self.rng.gauss(0, 0.008)
            out.append(round(max(0.0, min(1.0, v)), 4))
        return out

    def joystick(self, curls) -> dict:
        thumb = curls[0]
        return {
            "x": int(max(0, min(ADC_MAX, 2048 + self.rng.gauss(0, 14)))),
            "y": int(max(0, min(ADC_MAX,
                                2048 + (thumb - 0.5) * 1800 + self.rng.gauss(0, 14)))),
        }

    def imu(self) -> dict:
        return {"gyro": [int(self.rng.gauss(0, 10)) for _ in range(3)],
                "accel": [int(self.rng.gauss(0, 20)),
                          int(self.rng.gauss(0, 20)),
                          int(16393 + self.rng.gauss(0, 20))]}

    def meta(self, t: float) -> dict:
        vbat = max(3.3, 4.0 - 0.00003 * t)
        return {"vbat": round(vbat, 3),
                "pct": int(max(0, min(100, (vbat - 3.3) / 0.75 * 100))),
                "uptime_s": int(self._boot + t), "rssi": -48}


def _webcam_frame(t: float, curls) -> dict:
    """A /ws/webcam payload: the same hand, but in a hand-local frame (origin at
    the wrist) with identity rotations, which is all a single camera yields."""
    frame = hand_frame(t, device_id="webcam-right", handedness="right",
                       curls=curls)
    wrist = frame["joints"][0]["pos"]
    for j in frame["joints"]:
        j["pos"] = [round(p - w, 5) for p, w in zip(j["pos"], wrist)]
        j["rot"] = [0.0, 0.0, 0.0, 1.0]
    frame["meta"] = {"sim": True}
    return frame


# ---- the pure generator -----------------------------------------------------------

class RehearsalRig:
    """Everything the kit emits, as a pure function of time.

    tick(t) returns the packets for sim time t (seconds). It advances the noise
    streams, so call it with increasing t; the same config and seed with the
    same call sequence reproduces the same data.
    """

    def __init__(self, config: RehearsalConfig = None):
        self.config = config or RehearsalConfig()
        cfg = self.config
        self.bands = [
            _Band("%s-%s" % (cfg.device_prefix, side), side,
                  cfg.seed * 7919 + i * 104729)
            for i, side in enumerate(cfg.sides)
        ]
        self.lask5 = (_Lask5("lask5-sim", cfg.seed * 31 + 5)
                      if cfg.labeler == "lask5" else None)
        self._tick_no = 0
        # The role each band holds on itself (set by the set_role verb, reported
        # in get_info and the status frame). Bands start with none, like a new
        # kit. The LASK5 has no role verb; the hub tags it locally.
        self.device_roles = {}

    def set_device_role(self, device_id: str, role) -> str:
        """What the band's set_role verb does: store left / right / labeler, and
        clamp anything else to "" (no role). Returns the stored role."""
        role = (role or "").strip().lower() if isinstance(role, str) else ""
        if role not in ("left", "right", "labeler"):
            role = ""
        self.device_roles[device_id] = role
        return role

    @property
    def device_ids(self) -> list:
        ids = [b.device_id for b in self.bands]
        if self.lask5 is not None:
            ids.append(self.lask5.device_id)
        return ids

    @property
    def roles(self) -> dict:
        """Hub-side role tags this kit expects: bands left/right, LASK5 labeler."""
        roles = {b.device_id: b.side for b in self.bands}
        if self.lask5 is not None:
            roles[self.lask5.device_id] = "labeler"
        return roles

    def curls(self, t: float) -> tuple:
        return latent_curls(t)

    def _envelope(self, device_type: str, device_id: str, t: float, data: dict,
                  meta: dict = None) -> dict:
        pkt = {"v": "1.0", "type": device_type, "id": device_id,
               "ts": int(t * 1000) % 2 ** 31, "data": data}
        if meta:
            pkt["meta"] = meta
        return pkt

    def tick(self, t: float) -> dict:
        """Packets for time t: {"bands": [pkt..], "lask5": pkt|None,
        "hand": (ws_path, payload)|None}."""
        cfg = self.config
        noise = cfg.noise
        self._tick_no += 1
        status_due = (self._tick_no % max(1, int(round(cfg.rate_hz)))) == 1
        curls = latent_curls(t)
        band_curls = latent_curls(max(0.0, t + BAND_LEAD_S))

        bands = []
        for band in self.bands:
            data = {"matrix": band.matrix(band_curls, t, noise),
                    "rows": BAND_ROWS, "cols": BAND_COLS,
                    "imu": band.imu(t, noise)}
            bands.append(self._envelope(
                "flexgrid", band.device_id, t, data,
                band.meta(t, self.device_roles.get(band.device_id, ""))
                if status_due else None))

        lask5 = None
        if self.lask5 is not None:
            data = {"values": self.lask5.values(curls),
                    "joystick": self.lask5.joystick(curls),
                    "imu": self.lask5.imu()}
            lask5 = self._envelope("lask5", self.lask5.device_id, t, data,
                                   self.lask5.meta(t) if status_due else None)

        hand = None
        if cfg.labeler == "quest":
            hand = ("/ws/quest", hand_frame(t, curls=curls))
        elif cfg.labeler == "webcam":
            hand = ("/ws/webcam", _webcam_frame(t, curls))
        return {"bands": bands, "lask5": lask5, "hand": hand}

    # ---- discovery payloads ----

    def announce(self, device_id: str, cmd_port: int, udp_port: int) -> dict:
        """The V4 announce beacon for one device (no IP; the hub reads it from
        the datagram source)."""
        if self.lask5 is not None and device_id == self.lask5.device_id:
            return {"v": "1.0", "type": "announce", "id": device_id,
                    "role": "source", "dev": "lask5", "fw": FW_VERSION,
                    "transports": ["wifi"], "caps": ["label", "status", "cmd"],
                    "services": {"label": udp_port, "cmd": cmd_port},
                    "ts": int(time.time() * 1000) % 2 ** 31, "boot_seq": 1,
                    "pistons": 4, "joystick": True, "imu": dict(IMU_SCALE)}
        return {"v": "1.0", "type": "announce", "id": device_id,
                "role": "source", "dev": "flexgrid", "fw": FW_VERSION,
                "transports": ["wifi"], "caps": ["sensor", "status", "cmd"],
                "services": {"sensor": udp_port, "cmd": cmd_port},
                "ts": int(time.time() * 1000) % 2 ** 31, "boot_seq": 1,
                "matrix": [BAND_COLS, BAND_ROWS], "imu": dict(IMU_SCALE)}


# ---- transports ---------------------------------------------------------------------

SUBSCRIBER_TIMEOUT_S = 5.0        # firmware drops a subscriber after ~5 s silence
BEACON_INTERVAL_S = 1.0


class _SimDevice:
    """One simulated device's network identity: command port + subscriber list."""

    def __init__(self, sim, device_id: str, device_type: str, extra_info: dict):
        self.sim = sim
        self.device_id = device_id
        self.device_type = device_type
        self.extra_info = extra_info
        self.subscribers = {}          # (host, port) -> last heartbeat time
        self._lock = threading.Lock()
        self.cmd_port = 0
        self._server = None

    def start(self, bind_ip: str, port: int = 0):
        device = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    for line in self.rfile:
                        line = line.strip()
                        if line:
                            self.wfile.write(device.handle_line(line) + b"\n")
                            self.wfile.flush()
                except OSError:
                    pass

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server((bind_ip, port), Handler)
        self.cmd_port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True,
                         name="om-sim-cmd-" + self.device_id).start()

    def stop(self):
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    def has_subscribers(self) -> bool:
        now = time.time()
        with self._lock:
            for key in [k for k, ts in self.subscribers.items()
                        if now - ts > SUBSCRIBER_TIMEOUT_S]:
                del self.subscribers[key]
            return bool(self.subscribers)

    def handle_line(self, line: bytes) -> bytes:
        try:
            msg = json.loads(line.decode("utf-8"))
        except Exception as e:
            return self._ack(None, "error", {"message": "invalid_json: %s" % e})
        msg_id = msg.get("msg_id")
        data = msg.get("data") or {}
        verb = data.get("verb")
        try:
            key = (data.get("host"), int(data.get("port", 0)))
        except (TypeError, ValueError):
            return self._ack(msg_id, "error", {"verb": verb, "message": "bad port"})
        with self._lock:
            if verb == "subscribe":
                self.subscribers[key] = time.time()
                return self._ack(msg_id, "ok", {
                    "verb": verb, "accepted": True,
                    "subscriber_count": len(self.subscribers),
                    "max_subscribers": 4})
            if verb == "heartbeat":
                refreshed = key in self.subscribers
                if refreshed:
                    self.subscribers[key] = time.time()
                return self._ack(msg_id, "ok", {"verb": verb,
                                                "refreshed": refreshed})
            if verb == "unsubscribe":
                removed = self.subscribers.pop(key, None) is not None
                return self._ack(msg_id, "ok", {
                    "verb": verb, "removed": removed,
                    "subscriber_count": len(self.subscribers)})
            if verb == "get_info":
                info = {"verb": verb, "id": self.device_id,
                        "dev": self.device_type, "fw": FW_VERSION,
                        "caps": list(self.extra_info.get("caps", [])),
                        "subscribers": [{"host": h, "port": p}
                                        for h, p in self.subscribers]}
                info.update({k: v for k, v in self.extra_info.items()
                             if k != "caps"})
                # Like the band: the role key is absent while it has none.
                role = self.sim.rig.device_roles.get(self.device_id, "")
                if role:
                    info["role"] = role
                return self._ack(msg_id, "ok", info)
            if verb == "set_role" and self.device_type == "flexgrid":
                role = self.sim.rig.set_device_role(self.device_id,
                                                    data.get("role"))
                return self._ack(msg_id, "ok", {"verb": verb, "role": role})
            if verb in ("start_stream", "stop_stream"):
                return self._ack(msg_id, "ok", {"verb": verb})
        return self._ack(msg_id, "error", {"message": "unknown_verb: %s" % verb})

    @staticmethod
    def _ack(msg_id, status, data) -> bytes:
        return json.dumps({"v": "1.0", "type": "ack", "status": status,
                           "msg_id": msg_id, "data": data}).encode("utf-8")


class _LabelSocket:
    """Streams hand frames to the web server's /ws/quest or /ws/webcam from its
    own thread, so a slow or dead socket never stalls the UDP frames. Failures
    are printed (once per distinct message), never retried silently."""

    def __init__(self, scheme_pref: str, ip: str, port: int, log):
        self.scheme_pref = scheme_pref
        self.ip = ip
        self.port = port
        self.log = log
        self._frames = []              # latest-wins single slot per path
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="om-sim-label-ws")
        self._last_error = None
        self.sent = 0
        self.connected = threading.Event()
        self.scheme = None

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self._thread.join(timeout=3)

    def push(self, path: str, payload: dict):
        with self._cond:
            self._frames.append((path, json.dumps(payload)))
            if len(self._frames) > 200:          # drop the oldest if the socket lags
                del self._frames[:100]
            self._cond.notify()

    def _run(self):
        cm = None          # the websockets connect() context manager
        ws = None
        ws_path = None

        def close():
            nonlocal cm, ws
            try:
                if cm is not None:
                    cm.__exit__(None, None, None)
            except Exception:
                pass
            cm = ws = None

        while not self._stop.is_set():
            with self._cond:
                if not self._frames:
                    self._cond.wait(timeout=0.5)
                    continue
                path, text = self._frames.pop(0)
            try:
                if ws is None or ws_path != path:
                    close()
                    self.scheme = net.resolve_scheme(self.scheme_pref, self.ip,
                                                     self.port)
                    cm = net.ws_connect(self.scheme, self.ip, self.port, path)
                    ws = cm.__enter__()
                    ws_path = path
                    self._last_error = None
                    self.connected.set()
                    self.log("label stream connected: %s"
                             % net.ws_url(self.scheme, self.ip, self.port, path))
                ws.send(text)
                self.sent += 1
            except Exception as e:
                self.connected.clear()
                msg = "%s: %s" % (type(e).__name__, e)
                if msg != self._last_error:
                    url = net.ws_url(self.scheme or "http", self.ip, self.port,
                                     path)
                    self.log("label stream to %s failed (%s); retrying every 2 s. "
                             "Is `openmuscle web` running on port %d?"
                             % (url, msg, self.port))
                    self._last_error = msg
                close()
                self._stop.wait(2.0)
        close()


class RehearsalSimulator:
    """Runs a RehearsalRig as a live kit on the network.

    Frames go to target_ip:udp_port; beacons go to target_ip:announce_port while
    no hub holds a subscription (the firmware goes quiet once subscribed). The
    sim always sends frames to its target, whatever host a subscribe names, so it
    works with no network at all.

    role_setter: optional callable(device_id, role) -> truthy on success, used
    to tag the bands left/right and the LASK5 labeler without the Sources panel
    (in-process tests pass AppState.discovery.set_role, which tags on the hub
    only). When None and tag_roles is set, the roles are tagged through the web
    server's REST API, which also sends the bands their set_role verb: the sim
    bands then hold and report the role like real ones (get_info, status frame).
    The sim LASK5 has no set_role verb (hub-local tag, like the real labeler).
    """

    def __init__(self, config: RehearsalConfig = None, target_ip="127.0.0.1",
                 udp_port=3141, announce_port=3140, web_port=8000,
                 web_scheme="auto", speed=1.0, tag_roles=True,
                 role_setter=None, log=None):
        self.rig = RehearsalRig(config)
        self.config = self.rig.config
        self.target_ip = target_ip
        self.udp_port = udp_port
        self.announce_port = announce_port
        self.web_port = web_port
        self.web_scheme = web_scheme
        self.speed = float(speed)
        if not self.speed > 0:
            raise ValueError("speed must be > 0")
        self.tag_roles = tag_roles
        self.role_setter = role_setter
        self.log = log or (lambda msg: print(msg, flush=True))
        self.devices = {}              # device_id -> _SimDevice
        self.frames_sent = 0
        self.beacons_sent = 0
        self.tagged = threading.Event()
        self._stop = threading.Event()
        self._threads = []
        self._label_ws = None
        self._t0 = None
        self._sock = None

    # ---- lifecycle ----

    def start(self):
        if self._t0 is not None:
            return self
        loopback = self.target_ip.startswith("127.") or self.target_ip == "localhost"
        bind_ip = "127.0.0.1" if loopback else "0.0.0.0"
        for band in self.rig.bands:
            dev = _SimDevice(self, band.device_id, "flexgrid", {
                "caps": ["sensor", "status", "cmd"],
                "matrix": [BAND_COLS, BAND_ROWS], "imu": dict(IMU_SCALE)})
            dev.start(bind_ip)
            self.devices[band.device_id] = dev
        if self.rig.lask5 is not None:
            lid = self.rig.lask5.device_id
            dev = _SimDevice(self, lid, "lask5", {
                "caps": ["label", "status", "cmd"], "pistons": 4,
                "joystick": True, "imu": dict(IMU_SCALE)})
            dev.start(bind_ip)
            self.devices[lid] = dev
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if self.config.labeler != "lask5":
            self._label_ws = _LabelSocket(self.web_scheme, self.target_ip,
                                          self.web_port, self.log)
            self._label_ws.start()
        self._t0 = time.time()
        self._spawn(self._beacon_loop, "om-sim-beacon")
        self._spawn(self._stream_loop, "om-sim-stream")
        if self.tag_roles or self.role_setter is not None:
            self._spawn(self._tag_loop, "om-sim-tagger")
        else:
            self.tagged.set()
        return self

    def _spawn(self, fn, name):
        t = threading.Thread(target=fn, daemon=True, name=name)
        t.start()
        self._threads.append(t)

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=3)
        self._threads = []
        if self._label_ws is not None:
            self._label_ws.stop()
        for dev in self.devices.values():
            dev.stop()
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def sim_time(self) -> float:
        """Seconds of simulated time elapsed (wall time * speed)."""
        if self._t0 is None:
            return 0.0
        return (time.time() - self._t0) * self.speed

    def run_forever(self, duration: float = None):
        """Block until Ctrl+C (or `duration` sim seconds), then stop."""
        self.start()
        self.print_banner()
        try:
            while not self._stop.is_set():
                if duration is not None and self.sim_time() >= duration:
                    break
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            self.log("Stopped after %d frames, %d beacons"
                     % (self.frames_sent, self.beacons_sent))

    def print_banner(self):
        cfg = self.config
        self.log("Rehearsal kit: %d band(s) + %s labeler, noise x%g, %g Hz"
                 % (cfg.bands, cfg.labeler, cfg.noise, cfg.rate_hz))
        for did, dev in self.devices.items():
            self.log("  %-18s %-8s command port %d" % (did, dev.device_type,
                                                       dev.cmd_port))
        self.log("  frames -> UDP %s:%d, beacons -> UDP %s:%d (while unsubscribed)"
                 % (self.target_ip, self.udp_port, self.target_ip,
                    self.announce_port))
        if cfg.labeler != "lask5":
            self.log("  hand labels -> web server %s:%d" % (self.target_ip,
                                                            self.web_port))
        self.log("Press Ctrl+C to stop")

    # ---- loops ----

    def _beacon_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            while not self._stop.is_set():
                for did, dev in self.devices.items():
                    if dev.has_subscribers():
                        continue         # firmware stays quiet while held
                    payload = self.rig.announce(did, dev.cmd_port, self.udp_port)
                    try:
                        sock.sendto(json.dumps(payload).encode("utf-8"),
                                    (self.target_ip, self.announce_port))
                        self.beacons_sent += 1
                    except OSError as e:
                        self.log("beacon send failed: %s" % e)
                self._stop.wait(BEACON_INTERVAL_S)
        finally:
            sock.close()

    def _stream_loop(self):
        dt = 1.0 / self.config.rate_hz
        k = 0
        sock = self._sock
        dest = (self.target_ip, self.udp_port)
        while not self._stop.is_set():
            sim_t = k * dt
            wake = self._t0 + sim_t / self.speed
            delay = wake - time.time()
            if delay > 0:
                if self._stop.wait(delay):
                    break
            frame = self.rig.tick(sim_t)
            try:
                for pkt in frame["bands"]:
                    sock.sendto(json.dumps(pkt).encode("utf-8"), dest)
                    self.frames_sent += 1
                if frame["lask5"] is not None:
                    sock.sendto(json.dumps(frame["lask5"]).encode("utf-8"), dest)
                    self.frames_sent += 1
            except OSError as e:
                self.log("frame send failed: %s" % e)
                self._stop.wait(1.0)
            if frame["hand"] is not None and self._label_ws is not None:
                self._label_ws.push(*frame["hand"])
            k += 1
            if k % int(self.config.rate_hz * 10) == 0:
                self.log("sim t=%.0fs  frames sent: %d" % (sim_t, self.frames_sent))

    def _tag_loop(self):
        """Tag bands left/right and the LASK5 labeler on the hub once they have
        been discovered. Prints what it did; warns (once) if it never could."""
        want = dict(self.rig.roles)
        pending = dict(want)
        deadline = time.time() + 60.0
        scheme = None
        warned = False
        while pending and not self._stop.is_set():
            try:
                if self.role_setter is not None:
                    for did, role in list(pending.items()):
                        if self.role_setter(did, role):
                            self.log("tagged %s as %s" % (did, role))
                            del pending[did]
                else:
                    if scheme is None:
                        scheme = net.resolve_scheme(self.web_scheme,
                                                    self.target_ip, self.web_port)
                    known = {d.get("device_id") for d in net.http_json(
                        scheme, self.target_ip, self.web_port, "/api/discovery")}
                    for did, role in list(pending.items()):
                        if did in known:
                            net.http_json(scheme, self.target_ip, self.web_port,
                                          "/api/discovery/role",
                                          {"device_id": did, "role": role})
                            self.log("tagged %s as %s" % (did, role))
                            del pending[did]
            except Exception as e:
                if not warned and time.time() > deadline - 45:
                    self.log("could not reach the web UI to tag roles yet (%s: %s)"
                             % (type(e).__name__, e))
                    warned = True
            if pending and time.time() > deadline:
                self.log("could not tag %s within 60 s; set roles in the Sources "
                         "panel (left / right bands, labeler for the LASK5)"
                         % ", ".join("%s=%s" % kv for kv in pending.items()))
                break
            self._stop.wait(1.0)
        if not pending:
            self.tagged.set()

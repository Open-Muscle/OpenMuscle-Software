"""Shared application state for the OpenMuscle web UI.

A single AppState instance is shared across the FastAPI app for the lifetime
of the process. It owns:
 - the UDP listener thread (consumes packets into a Queue)
 - a registry of devices currently streaming (with last-seen + rate stats)
 - the latest matrix frame per device (for new WebSocket clients to render
   immediately on connect, without having to wait for the next packet)
 - the currently-active recording (if any)
 - a set of connected WebSocket clients to broadcast frames to
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Optional

import socket

from openmuscle.data.storage import CaptureWriter
from openmuscle.protocol.schema import CURRENT_VERSION, OpenMusclePacket


# Canonical channel ordering for a single quest_hand joint. This is the
# coupling point between two places:
#   - `AppState.ingest_quest_packet` packs joints into `data.values` in
#     this order via `_flatten_quest_joint`.
#   - `AppState._write_labels_schema` emits the column->(joint, channel)
#     map using `_quest_label_column` which assumes this same order.
# Changing one without the other makes the labels-schema sidecar lie
# about what's in the CSV. Both callers route through this tuple + the
# two helpers below to make the coupling structural rather than two
# aspirational comments.
QUEST_JOINT_CHANNEL_ORDER = ("px", "py", "pz", "rx", "ry", "rz", "rw")


def _flatten_quest_joint(pos, rot):
    """Pack one joint's (pos, rot) into the canonical channel order.

    `pos` is [x, y, z]; `rot` is a unit quaternion [x, y, z, w]. Returns a
    flat list of `len(QUEST_JOINT_CHANNEL_ORDER)` floats.
    """
    components = {"px": pos[0], "py": pos[1], "pz": pos[2],
                  "rx": rot[0], "ry": rot[1], "rz": rot[2], "rw": rot[3]}
    return [components[ch] for ch in QUEST_JOINT_CHANNEL_ORDER]


def _quest_label_column(joint_index: int, channel_index: int) -> int:
    """Index of the CSV column corresponding to (joint, channel) for a
    quest_hand recording. Inverse view of the layout produced by
    `_flatten_quest_joint` applied N times."""
    return joint_index * len(QUEST_JOINT_CHANNEL_ORDER) + channel_index
from openmuscle.receiver.matcher import TemporalMatcher
from openmuscle.receiver.udp_listener import UDPListener
from openmuscle.discovery import DiscoveryManager
from openmuscle.web.inference import InferenceEngine
from openmuscle.web.log_buffer import LogBuffer, install as install_log_handler


@dataclass
class DeviceInfo:
    device_id: str
    device_type: str
    src_addr: str = ""           # "ip:port" of last packet
    rows: int = 0
    cols: int = 0
    last_seen: float = 0.0
    packets_total: int = 0
    # Rolling rate sample
    _window_start: float = 0.0
    _window_count: int = 0
    hz: float = 0.0
    last_matrix: list = field(default_factory=list)   # [cols][rows] flexgrid
    last_values: list = field(default_factory=list)   # generic 1-D payload (LASK5 pistons, sensorband, ...)
    last_joystick: dict = field(default_factory=dict) # LASK5 joystick {"x": v, "y": v}
    # Fast IMU path: data.imu = {gyro[3], accel[3]} on every flexgrid frame
    # (PROTOCOL.md 7.1, ~18-20Hz). The gyro/orientation viz reads this, not the
    # ~1Hz status meta.imu.
    last_imu: dict = field(default_factory=dict)
    # Device-status telemetry from the v1.0 packet's `meta` field. Stays
    # whatever the device last reported -- a missing key in a later packet
    # doesn't clobber an earlier value. Common keys: vbat, pct, uptime_s,
    # free_mem, rssi, imu, reset_cause, reset_cause_name.
    status: dict = field(default_factory=dict)
    status_updated_at: float = 0.0
    # Reboot detection: when meta.uptime_s arrives lower than the previously
    # seen value, the device must have restarted in between. We bump
    # reboot_count and stamp the wall-clock time. The web UI surfaces this
    # so an unexpected reset mid-recording is impossible to miss.
    reboot_count: int = 0
    last_reboot_at: float = 0.0
    last_reset_cause: Optional[str] = None

    def update(self, pkt: OpenMusclePacket, src_addr: tuple):
        self.device_type = pkt.device_type
        self.src_addr = f"{src_addr[0]}:{src_addr[1]}"
        self.last_seen = pkt.receive_time
        self.packets_total += 1

        # Track shape for flexgrid-style matrix payloads
        mat = pkt.data.get("matrix")
        if mat:
            self.last_matrix = mat
            self.cols = len(mat)
            self.rows = len(mat[0]) if mat else 0

        # Track LASK5 / sensorband-style 1-D payloads.
        # Both the new protocol ("data": {"values": [...]}) and legacy
        # parser-side mapping put the array in pkt.data["values"].
        vals = pkt.data.get("values")
        if vals:
            self.last_values = list(vals)

        joy = pkt.data.get("joystick")
        if isinstance(joy, dict):
            self.last_joystick = joy

        # Fast IMU path: data.imu {gyro[3], accel[3]} on every flexgrid frame
        # (~18-20Hz) drives the gyro/orientation viz. Distinct from the ~1Hz
        # status meta.imu (kept for the device card).
        imu = pkt.data.get("imu")
        if isinstance(imu, dict):
            self.last_imu = imu

        # Telemetry from the v1.0 envelope's `meta` field -- e.g. {vbat, pct,
        # uptime_s, free_mem, rssi, imu, reset_cause_name}. Merge keys (don't
        # overwrite the whole dict) so a status update with a subset of keys
        # doesn't wipe previously-reported ones.
        meta = pkt.metadata
        if isinstance(meta, dict) and meta:
            # Reboot detection: an uptime_s that goes BACKWARDS means the
            # device restarted. We allow a small slack (2s) to absorb clock
            # jitter on the device side. This is the smoking-gun signal --
            # if it fires mid-recording, the device crashed/brownout-reset.
            new_uptime = meta.get("uptime_s")
            prev_uptime = self.status.get("uptime_s")
            if (isinstance(new_uptime, (int, float))
                    and isinstance(prev_uptime, (int, float))
                    and new_uptime + 2 < prev_uptime):
                self.reboot_count += 1
                self.last_reboot_at = pkt.receive_time
                # Remember WHY this boot started -- the device reports it in
                # the first meta-bearing packet after the reset.
                rc = meta.get("reset_cause_name") or meta.get("reset_cause")
                if rc is not None:
                    self.last_reset_cause = str(rc)
            for k, v in meta.items():
                self.status[k] = v
            self.status_updated_at = pkt.receive_time

        # Rate (1-second sliding window)
        now = pkt.receive_time
        if self._window_start == 0.0:
            self._window_start = now
            self._window_count = 0
        self._window_count += 1
        elapsed = now - self._window_start
        if elapsed >= 1.0:
            self.hz = self._window_count / elapsed
            self._window_start = now
            self._window_count = 0


@dataclass
class ActiveCapture:
    """An in-progress paired recording.

    Architecturally three files are written concurrently:
      - `<name>.csv`           paired rows (timestamp + sensor matrix + label values),
                               only emitted when a sensor frame found a label within the
                               temporal window. This is the file training reads.
      - `<name>.sensor.jsonl`  raw sensor packets (one per line, with receive_time).
      - `<name>.label.jsonl`   raw label packets (one per line, with receive_time).
    The two JSONL sidecars let us re-pair offline with a different window without
    needing to re-capture. They're cheap to produce (~10-50 KB/s at typical rates).
    """
    writer: CaptureWriter
    path: Path
    sensor_device_id: str
    label_device_id: Optional[str]
    started_at: float
    rows: int           # sensor matrix shape
    cols: int
    window_s: float
    matcher: TemporalMatcher
    # Hub-assigned role for the PRIMARY sensor source in schema-v2 captures (one
    # of the lowercase wire tokens left/right/labeler; board #0091).
    role: str = "left"
    # All recorded bands as {device_id: role}. Single-source = one entry (the
    # primary). Multi-band capture adds more (each a feature source tagged with
    # its own role); every band's frames are matched against the one labeler and
    # written as its own interleaved v2 row. Defaults to {} and is filled in by
    # start_recording so existing constructions stay valid.
    sensors: dict = field(default_factory=dict)
    sensor_jsonl: Optional[IO] = None
    label_jsonl: Optional[IO] = None
    # Path for a per-capture labels-schema sidecar. Written on the first
    # label packet (lazy, like the CSV header) so the schema reflects
    # whatever the device actually sent rather than what we expected.
    # Only populated for label sources whose label width / structure is
    # not derivable from device_type alone -- e.g. quest_hand.
    labels_schema_path: Optional[Path] = None
    labels_schema_written: bool = False
    # Label-width lock. quest_hand frames can vary in joint count when hand
    # tracking is partial; writing those varying lengths straight to the CSV
    # produces ragged rows (different column count per row) that break
    # pandas.read_csv and corrupt the capture for training. We lock the
    # expected width at the first label packet (same width the labels-schema
    # sidecar describes) and pad/truncate every paired row to it, so the CSV
    # stays rectangular and consistent with the schema. None until locked.
    locked_label_count: Optional[int] = None
    label_width_mismatch_count: int = 0
    # Bilateral two-hand capture: each band is matched to the labeler of ITS OWN
    # side, so the left band's rows carry the left-hand label and the right
    # band's rows carry the right-hand label (each tagged with its role). Empty
    # for single-labeler captures, which keep using `matcher`/`label_device_id`.
    # side_matchers: {"left": TemporalMatcher, "right": TemporalMatcher}.
    # label_id_side: {labeler_device_id: "left"/"right"} for routing label frames.
    side_matchers: dict = field(default_factory=dict)
    label_id_side: dict = field(default_factory=dict)
    # Stats surfaced in the WS snapshot
    sensor_frames_seen: int = 0
    label_packets_seen: int = 0
    matched_count: int = 0
    unpaired_sensor_count: int = 0

    @property
    def row_count(self) -> int:
        return self.writer.row_count

    @property
    def duration_s(self) -> float:
        return time.time() - self.started_at

    @property
    def match_rate(self) -> float:
        """Fraction of sensor frames that found a label within window."""
        if self.sensor_frames_seen == 0:
            return 0.0
        return self.matched_count / self.sensor_frames_seen


class AppState:
    """Owns the UDP listener, device registry, current recording, WS clients."""

    def __init__(self, udp_port: int = 3141, captures_dir: Path | None = None,
                 model_path: Optional[str] = None,
                 hand_target: Optional[tuple] = None,
                 enable_discovery: bool = True,
                 discovery_announce_port: int = 3140):
        """
        Args:
            udp_port: incoming device telemetry port (FlexGrid + LASK5)
            captures_dir: where recorded CSVs go
            enable_discovery: run native V4 discovery + auto-subscribe (replaces
                        the interim bridge_subscriber.py). Set False to bind the
                        UDP port only and rely on an external subscriber/bridge.
            model_path: optional path to a trained model .pkl. When set, every
                        FlexGrid packet is run through the model and its
                        predictions populate the WS snapshot's `inference`
                        field.
            hand_target: optional (host, port) tuple. When BOTH model_path and
                         hand_target are set, predictions are forwarded over
                         UDP to the robot hand in its `PC,a1,a2,a3,a4,a5`
                         CSV-with-device-id format.
        """
        self.udp_port = udp_port
        self.captures_dir = Path(captures_dir or Path("data/raw/merged"))
        self.captures_dir.mkdir(parents=True, exist_ok=True)

        # Log buffer: catches uvicorn/FastAPI/our own `logging` records into
        # a ring buffer the UI can poll. Installed before anything else so
        # we don't miss startup errors.
        self.log_buffer = LogBuffer(capacity=400)
        install_log_handler(self.log_buffer)
        self.log_buffer.info("server", "AppState init  udp_port={}  captures_dir={}".format(
            udp_port, str(self.captures_dir)))

        # Native V4 discovery + subscribe. Discovers announcing sources, keeps a
        # TCP subscription + 1 Hz heartbeat to each, and re-probes cached devices
        # on startup (a device's beacon goes silent once any hub subscribes, so
        # the cache is how we recover one another hub already holds). Replaces
        # pc/bridge_subscriber.py. The listener hands announce beacons straight
        # to it; subscribed devices' sensor frames flow through the normal queue.
        self.discovery: Optional[DiscoveryManager] = (
            DiscoveryManager(udp_port=udp_port,
                             announce_port=discovery_announce_port,
                             auto_subscribe=True)
            if enable_discovery else None)
        announce_handler = self.discovery.on_announce if self.discovery else None
        self.listener = UDPListener(port=udp_port, announce_handler=announce_handler)
        self.devices: dict[str, DeviceInfo] = {}
        self.recording: Optional[ActiveCapture] = None
        # At most one active session at a time. Recordings made while a
        # session is active get their meta.auto.session_id auto-populated.
        self.active_session: Optional[dict] = None
        self.ws_clients: set = set()
        self._broadcast_task: Optional[asyncio.Task] = None
        self._started = False

        # ----- inference -----
        self.engine: Optional[InferenceEngine] = None
        self.engine_status: str = "no model loaded"
        if model_path:
            try:
                self.engine = InferenceEngine(model_path)
                self.engine_status = "loaded"
                self.log_buffer.info("inference", "model loaded from CLI: {}".format(self.engine.name))
            except Exception as e:
                # Don't crash the server on bad model; show the error in the UI.
                self.engine_status = "load failed: {}".format(e)
                self.log_buffer.error("inference", "model load failed at startup: {}".format(e))

        # Runtime on/off for inference. Defaults to True iff a model was
        # passed at startup -- if you launched `openmuscle web` without
        # --model, inference stays paused until you load + Resume in the UI.
        self.inference_enabled: bool = self.engine is not None

        # Forwarding socket to robot hand. Opened lazily.
        self.hand_target: Optional[tuple] = hand_target
        self._hand_sock: Optional[socket.socket] = None

        # Most recent prediction surfaced in the WS snapshot
        self._last_inference_values: Optional[list] = None
        self._last_inference_ts: float = 0.0

    # ----- lifecycle -----

    def start(self):
        if self._started:
            return
        self.listener.start()
        if self.discovery:
            # Starts the dedicated announce-beacon listener (UDP 3140) and
            # re-probes cached devices so we recover sources whose beacon is
            # silent (held by another hub) at startup.
            self.discovery.start()
        self._started = True

    def stop(self):
        self.listener.stop()
        if self.discovery:
            self.discovery.stop()
        if self.recording:
            self.stop_recording()
        if self._hand_sock is not None:
            try:
                self._hand_sock.close()
            except Exception:
                pass
            self._hand_sock = None

    async def run_broadcaster(self):
        """Background task that drains the listener queue and pushes frames
        to all connected WebSocket clients (and to the active capture writer,
        if recording)."""
        loop = asyncio.get_event_loop()
        while True:
            # Pull packets in batches so a burst doesn't starve the WS loop.
            packets = []
            try:
                # Block briefly in a thread so we yield to asyncio
                pkt = await loop.run_in_executor(None, self.listener.packet_queue.get)
                packets.append(pkt)
                # Drain anything else immediately available
                while not self.listener.packet_queue.empty():
                    packets.append(self.listener.packet_queue.get_nowait())
            except Exception:
                await asyncio.sleep(0.01)
                continue

            for pkt in packets:
                self._handle_packet(pkt)

            await self._broadcast_latest_frames()

    def _handle_packet(self, pkt: OpenMusclePacket):
        # The UDPListener doesn't expose the src addr through the queue
        # (it just enqueues the parsed packet). We'll synthesize one from
        # device_id for display purposes if needed.
        dev = self.devices.get(pkt.device_id)
        if dev is None:
            dev = DeviceInfo(device_id=pkt.device_id, device_type=pkt.device_type)
            self.devices[pkt.device_id] = dev
        dev.update(pkt, ("", 0))

        # Recording: dispatch this packet to the matcher/writer/sidecars.
        if self.recording is not None:
            self._record_packet(pkt)

        # Run inference on flexgrid frames if an engine is loaded and not
        # paused. We do this synchronously in the same thread as packet
        # handling -- RF predict is ~ms, well under the 40ms inter-frame
        # budget at 25Hz.
        if (self.engine and self.inference_enabled
                and pkt.device_type == "flexgrid"):
            mat = pkt.data.get("matrix")
            if mat:
                pred = self.engine.predict(mat)
                if pred is not None:
                    self._last_inference_values = pred
                    self._last_inference_ts = time.time()
                    if self.hand_target:
                        self._forward_to_hand(pred)

    def ingest_quest_packet(self, payload: dict) -> None:
        """Synthesize an OpenMusclePacket from a Quest WebSocket frame and
        route it through the standard packet path.

        From the recorder's perspective the Quest is just another device:
        once we build an OpenMusclePacket with `device_type="quest_hand"`
        and hand it to `_handle_packet`, the DeviceInfo registry, the
        TemporalMatcher, the JSONL sidecars, and the WS snapshot all
        treat it identically to LASK5. This is why the Quest never needs
        to learn UDP -- the JS in the headset only has WebSocket.

        Expected payload shape (one hand for v1, per the team's
        "FlexGrid-arm only" decision):

            {
                "device_id":   "quest-01",          # optional
                "ts":          12345,               # device-local ms (optional)
                "handedness":  "left" | "right",    # which hand this frame is for
                "joints": [
                    {"name": "wrist",          "pos": [x,y,z], "rot": [x,y,z,w], "radius": 0.02},
                    {"name": "thumb-metacarpal", "pos": [...], "rot": [...]},
                    ... 26 entries total, in OpenXR canonical order
                ],
                "meta": {...}                       # optional, e.g. tracking confidence
            }

        We flatten joints into `data.values = [px,py,pz, rx,ry,rz,rw] * N`
        so it matches LASK5's `data.values` convention (the recorder pulls
        from this field). The structured per-joint form is preserved under
        `data.hands` for the JSONL sidecar -- if you later want to know
        WHICH joint a column corresponds to, the sidecar tells you, while
        the trainable CSV stays a plain matrix of floats.

        Empty payloads (e.g. headset reports tracking lost this frame)
        are silently dropped -- we want gaps in the data, not zero rows
        that would mislead the model.
        """
        joints = payload.get("joints") or []
        if not joints:
            return

        flat: list[float] = []
        joint_names: list[str] = []
        for j in joints:
            pos = j.get("pos") or [0.0, 0.0, 0.0]
            rot = j.get("rot") or [0.0, 0.0, 0.0, 1.0]
            # Route through _flatten_quest_joint so the channel order is
            # taken from QUEST_JOINT_CHANNEL_ORDER -- same source-of-truth
            # the labels-schema sidecar reads via _quest_label_column.
            flat.extend(float(v) for v in _flatten_quest_joint(pos, rot))
            joint_names.append(j.get("name", ""))

        pkt = OpenMusclePacket(
            version=CURRENT_VERSION,
            device_type="quest_hand",
            device_id=payload.get("device_id") or "quest-01",
            timestamp_ms=int(payload.get("ts") or 0),
            data={
                "values": flat,
                "handedness": payload.get("handedness") or "unknown",
                "joint_names": joint_names,
                "hands": {
                    "handedness": payload.get("handedness") or "unknown",
                    "joints": joints,
                },
            },
            metadata=payload.get("meta") or {},
            receive_time=time.time(),
        )
        self._handle_packet(pkt)

    # Flush JSONL sidecars every N frames to bound crash-loss to ~3 s of
    # data while keeping syscalls ~50× cheaper than line-buffered writes.
    # At 33 Hz sensor + 25 Hz label rates, 100 ≈ 3 s.
    JSONL_FLUSH_EVERY = 100

    def _record_packet(self, pkt: OpenMusclePacket):
        """Route a packet to the active capture: sidecars + matcher + paired CSV."""
        rec = self.recording
        if rec is None:
            return

        # --- Bilateral label stream: route to the matcher of its OWN side, so
        # the left-hand labeler feeds the left band and the right-hand labeler
        # feeds the right band (two-hand capture). ---
        if rec.label_id_side and pkt.device_id in rec.label_id_side:
            rec.side_matchers[rec.label_id_side[pkt.device_id]].add_label(pkt)
            rec.label_packets_seen += 1
            self._write_jsonl(rec.label_jsonl, pkt)
            if rec.labels_schema_path is not None and not rec.labels_schema_written:
                self._write_labels_schema(rec, pkt)
            if (rec.label_packets_seen % self.JSONL_FLUSH_EVERY == 0
                    and rec.label_jsonl is not None):
                try:
                    rec.label_jsonl.flush()
                except Exception:
                    pass
            return

        # --- Label stream: append to matcher and JSONL sidecar ---
        if rec.label_device_id is not None and pkt.device_id == rec.label_device_id:
            rec.matcher.add_label(pkt)
            rec.label_packets_seen += 1
            self._write_jsonl(rec.label_jsonl, pkt)
            # Lazy: emit the labels-schema sidecar on the first label packet
            # for label sources whose column layout is opaque from device_type
            # alone. v1: quest_hand only. The sidecar gives consumers a map
            # from the CSV's label_0..label_N columns back to (joint, channel).
            if rec.labels_schema_path is not None and not rec.labels_schema_written:
                self._write_labels_schema(rec, pkt)
            # Bounded crash-loss flush
            if (rec.label_packets_seen % self.JSONL_FLUSH_EVERY == 0
                    and rec.label_jsonl is not None):
                try:
                    rec.label_jsonl.flush()
                except Exception:
                    pass
            return

        # --- Sensor stream: write JSONL sidecar always, paired CSV when matched ---
        # Multi-band: any recorded band (device_id in rec.sensors) produces rows,
        # each tagged with its own role. Single-source = one entry in rec.sensors.
        if pkt.device_id not in rec.sensors:
            return  # ignore packets from third-party devices during this recording

        mat = pkt.data.get("matrix")
        if not mat:
            return  # nothing to record for non-matrix sensor payloads yet

        rec.sensor_frames_seen += 1
        self._write_jsonl(rec.sensor_jsonl, pkt)
        if (rec.sensor_frames_seen % self.JSONL_FLUSH_EVERY == 0
                and rec.sensor_jsonl is not None):
            try:
                rec.sensor_jsonl.flush()
            except Exception:
                pass

        # Try to pair this sensor frame with the closest label in window
        label_imu = None
        if rec.side_matchers:
            # Bilateral: match this band against the labeler of its OWN side, so
            # the left band gets the left hand and the right band the right hand.
            matcher = rec.side_matchers.get(rec.sensors.get(pkt.device_id))
            if matcher is None:
                label_values = []      # band with no same-side labeler: sensor-only
            else:
                matched = matcher.match(pkt)
                if matched is None:
                    rec.unpaired_sensor_count += 1
                    return
                rec.matched_count += 1
                label_values = list(matched.data.get("values", []))
                label_imu = matched.data.get("imu")
        elif rec.label_device_id is None:
            # Sensor-only mode (no label device): just write sensor + empty labels.
            label_values = []
        else:
            matched = rec.matcher.match(pkt)
            if matched is None:
                rec.unpaired_sensor_count += 1
                return  # drop unpaired sensor frames from the paired CSV
            rec.matched_count += 1
            label_values = list(matched.data.get("values", []))
            # The matched labeler's IMU = the orientation ground-truth (e.g. a
            # LASK5 gyro for supination). Recorded into the lbl_imu_* columns.
            label_imu = matched.data.get("imu")

        # Guarantee a rectangular CSV. If the label width was locked (quest_hand)
        # and this matched label has a different length, pad with zeros or
        # truncate to the locked width. Without this, variable-length payloads
        # (partial hand tracking, or a misbehaving client) would write ragged
        # rows that break pandas.read_csv and corrupt the whole capture. The
        # locked width matches the labels-schema sidecar, so consumers can
        # still map every column to a (joint, channel). NB: a well-behaved
        # client sends a fixed-length array every frame, so this rarely fires;
        # the mismatch counter surfaces it in the stop-recording stats + log
        # if it does.
        if rec.locked_label_count is not None and label_values:
            n = rec.locked_label_count
            if len(label_values) != n:
                rec.label_width_mismatch_count += 1
                if len(label_values) < n:
                    label_values = list(label_values) + [0.0] * (n - len(label_values))
                else:
                    label_values = list(label_values[:n])

        # Flatten as-sent [cols][rows] matrix row-major. Header in CaptureWriter
        # is R0C0..R0Cn, R1C0.., so iterating rows-then-cols here keeps the
        # column meaning correct (cf. the col-major bug we fixed in 245cb8f).
        rows = len(mat[0])
        cols = len(mat)
        flat = [mat[c][r] for r in range(rows) for c in range(cols)]
        # Schema v2: one long row per sensor frame, tagged with the source's
        # role + device_id and a hub-arrival epoch-ms timestamp. Features are
        # already row-major (above); labels are the matched label vector.
        ts_hub_ms = int(pkt.receive_time * 1000)
        rec.writer.write_row_v2(ts_hub_ms, rec.sensors[pkt.device_id],
                                pkt.device_id, flat, label_values,
                                sensor_imu=pkt.data.get("imu"),
                                label_imu=label_imu)

    def _write_labels_schema(self, rec: "ActiveCapture", pkt: OpenMusclePacket) -> None:
        """Emit the per-capture labels-schema sidecar.

        Maps the CSV's label_0..label_N columns back to the underlying
        (joint, channel) coordinates for a quest_hand recording. Without
        this a wide-label CSV is opaque -- you'd have to know the joint
        ordering by convention. With it, any consumer can deserialize
        label columns into named joint poses.

        TODO(wrist-relative-labels): joint positions are stored in absolute
        world coordinates as captured by the headset, so a model trained
        on them learns to predict positions where the recordings happened
        to be -- generally not where the user is at inference time. The
        VR ghost-hand viz works around this by anchoring predicted joints
        to the real wrist each frame, but the right long-term fix is to
        subtract the wrist position (and optionally rotate into the
        wrist's frame) before writing, and reverse the transform at
        inference. This changes the CSV semantics, so it deserves its
        own scope. Track in the OpenMuscle-Software repo issue list
        ("Wrist-relative label coordinates for portable quest_hand
        models") before any model that needs to generalize across
        capture-locations.
        """
        if rec.labels_schema_path is None:
            return
        joint_names = list(pkt.data.get("joint_names") or [])
        handedness = pkt.data.get("handedness") or "unknown"
        # Pull the channel order from the module-level constant so the schema
        # is guaranteed consistent with how ingest_quest_packet flattened
        # the values into the CSV (see QUEST_JOINT_CHANNEL_ORDER docstring).
        ordering = list(QUEST_JOINT_CHANNEL_ORDER)
        n_floats = len(ordering)
        # Build the explicit column->(joint, channel) map so consumers
        # don't have to re-derive it from joint order.
        columns = []
        for ji, jn in enumerate(joint_names):
            for ci, ch in enumerate(ordering):
                columns.append({
                    "name": f"label_{_quest_label_column(ji, ci)}",
                    "joint": jn,
                    "channel": ch,
                })
        n_label_columns = len(joint_names) * n_floats
        # Lock the CSV label width to what this first label packet described,
        # so _record_packet can pad/truncate every paired row to match the
        # schema and keep the CSV rectangular. (Well-behaved clients send a
        # fixed-length joint array every frame, so this almost never triggers
        # a pad/truncate -- it's a safety net against partial-tracking frames
        # and any client that emits a variable-length payload.)
        rec.locked_label_count = n_label_columns
        schema = {
            "label_source": "quest_hand",
            "handedness": handedness,
            "ordering": ordering,
            "floats_per_joint": n_floats,
            "n_joints": len(joint_names),
            "n_label_columns": n_label_columns,
            "joint_names": joint_names,
            "columns": columns,
        }
        try:
            with open(rec.labels_schema_path, "w") as f:
                json.dump(schema, f, indent=2)
            rec.labels_schema_written = True
            self.log_buffer.info(
                "recording",
                f"labels-schema written: {rec.labels_schema_path.name} "
                f"({len(joint_names)} joints, {len(columns)} columns)")
        except OSError as e:
            self.log_buffer.warn(
                "recording", f"labels-schema write failed: {e}")

    @staticmethod
    def _write_jsonl(stream: Optional[IO], pkt: OpenMusclePacket):
        """Append one packet as a JSONL line. No-op if stream is None.

        Includes pkt.metadata so post-mortem analysis of a recording can
        spot device reboots / battery dips / wifi RSSI drops at the moment
        the recording stopped. This is the difference between 'why did the
        device stop' and 'no idea, packets just ended'.
        """
        if stream is None:
            return
        try:
            row = {
                "t": pkt.receive_time,
                "device_id": pkt.device_id,
                "device_type": pkt.device_type,
                "data": pkt.data,
            }
            # Only include `meta` when present -- otherwise we'd add an
            # empty dict to every line, wasting disk.
            if pkt.metadata:
                row["meta"] = pkt.metadata
            stream.write(json.dumps(row) + "\n")
        except Exception:
            # Don't kill the recording on a serialization hiccup; the paired
            # CSV is the authoritative file.
            pass

    def _forward_to_hand(self, pred: list):
        """Send the prediction to the robot hand as a `PC,...` UDP datagram.

        Builds 5 servo angles in 0..179. Channel order on the hand
        (FINGER_CHANNELS = [1, 3, 5, 7, 9]) is anatomically:
            channel 1 -> thumb
            channel 3 -> index
            channel 5 -> middle
            channel 7 -> ring
            channel 9 -> pinky

        The LASK5 has 4 pistons (the 4 closing fingers) and a joystick.
        We map joystick X -> thumb, pistons 0..3 -> index..pinky.
        The hand's 'PC' device config uses linear 0..179 -> 0..179, so values
        land directly on servo angles.
        """
        # Thumb (channel 1) = joystick X from the most recent LASK5 packet.
        # Range 0..4095 -> 0..179. Default to center (90) if no LASK5 has been
        # seen yet (so the thumb sits in a neutral pose instead of slamming open).
        joy_x = None
        for d in self.devices.values():
            if d.device_type == "lask5" and d.last_joystick:
                jx = d.last_joystick.get("x")
                if isinstance(jx, (int, float)):
                    joy_x = jx
                    break
        thumb_angle = 90 if joy_x is None else max(0, min(179, int((joy_x / 4095.0) * 179)))

        # Index..pinky (channels 3, 5, 7, 9) from pistons 0..3.
        # Model output is assumed normalized 0..1; anything else gets clamped,
        # which is the right failure mode -- finger goes to extreme rather
        # than commanding a 4000-degree servo angle.
        finger_angles = []
        for v in pred[:4]:
            try:
                v = max(0.0, min(1.0, float(v)))
            except Exception:
                v = 0.0
            finger_angles.append(int(v * 179))

        # The hand's 'PC' device config has reverse=False, but the LASK5's
        # native ESPNow path uses the 'default' / 'L5' config (reverse=True)
        # which flips the piston order before mapping to FINGER_CHANNELS.
        # To match that mapping from our PC path, we reverse the pistons
        # ourselves: P1 -> index, P2 -> middle, P3 -> ring, P4 -> pinky.
        # (Documented in DEVICES of the hand firmware, archived 2026-05-14.)
        angles = [thumb_angle] + finger_angles[::-1]   # [thumb, P4, P3, P2, P1]

        # Build the CSV the hand expects: 'PC,a1,a2,a3,a4,a5'
        payload = ("PC," + ",".join(str(a) for a in angles)).encode("utf-8")

        # Rate-limited log so we can SEE whether forwarding is working.
        # First time + every 500th packet: log success/failure to the buffer
        # so the operator can debug without strace.
        self._hand_forward_count = getattr(self, "_hand_forward_count", 0) + 1
        log_now = (self._hand_forward_count == 1
                   or self._hand_forward_count % 500 == 0)
        try:
            if self._hand_sock is None:
                self._hand_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._hand_sock.setblocking(False)
            n = self._hand_sock.sendto(payload, self.hand_target)
            if log_now:
                self.log_buffer.info("inference",
                    "hand forward #{}: sent {} bytes to {}:{} -> {!r}".format(
                        self._hand_forward_count, n,
                        self.hand_target[0], self.hand_target[1],
                        payload.decode("utf-8", errors="replace")))
        except Exception as e:
            # Always log the first failure so the operator sees it; rate-limit
            # subsequent ones (every 500) so we don't spam.
            if log_now or not getattr(self, "_hand_forward_error_logged", False):
                self.log_buffer.warn("inference",
                    "hand forward #{} FAILED: {} ({!r}) -> target={}".format(
                        self._hand_forward_count, type(e).__name__, str(e),
                        self.hand_target))
                self._hand_forward_error_logged = True

    async def _broadcast_latest_frames(self):
        """Push the latest frame for each device to all WS clients."""
        if not self.ws_clients:
            return
        payload = self._snapshot()
        dead = []
        for ws in list(self.ws_clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)

    def _snapshot(self) -> dict:
        """Snapshot of all devices + recording status for clients."""
        devices_out = []
        for d in self.devices.values():
            status_age = (round(time.time() - d.status_updated_at, 2)
                          if d.status_updated_at else None)
            devices_out.append({
                "device_id": d.device_id,
                "device_type": d.device_type,
                "rows": d.rows,
                "cols": d.cols,
                "hz": round(d.hz, 1),
                "packets": d.packets_total,
                "last_seen_age": round(time.time() - d.last_seen, 2),
                "matrix": d.last_matrix,
                "values": d.last_values,         # LASK5 piston ADCs etc.
                "joystick": d.last_joystick,     # LASK5 joystick {"x", "y"}
                "imu": d.last_imu,               # fast data.imu {gyro[3], accel[3]}
                "status": d.status if d.status else None,   # vbat/pct/uptime/...
                "status_age": status_age,
                "reboot_count": d.reboot_count,
                "last_reboot_age": (round(time.time() - d.last_reboot_at, 1)
                                    if d.last_reboot_at else None),
                "last_reset_cause": d.last_reset_cause,
            })
        rec = None
        if self.recording:
            r = self.recording
            rec = {
                "filename": r.path.name,
                "schema_version": "v2",
                "role": r.role,
                "sensors": dict(r.sensors),   # {device_id: role} for every band
                "sensor_device_id": r.sensor_device_id,
                "label_device_id": r.label_device_id,
                "rows": r.row_count,
                "duration_s": round(r.duration_s, 1),
                "shape": [r.rows, r.cols],
                "window_ms": int(r.window_s * 1000),
                # Match-quality counters, updated every frame
                "matched": r.matched_count,
                "unpaired_sensor": r.unpaired_sensor_count,
                "sensor_frames_seen": r.sensor_frames_seen,
                "label_packets_seen": r.label_packets_seen,
                "match_rate": round(r.match_rate, 3),
                # Rows that had to be padded/truncated to the locked label
                # width (variable-length label frames, e.g. partial hand
                # tracking). Surfaced live so the in-VR header can warn the
                # operator that joints are dropping mid-capture.
                "label_width_mismatch": r.label_width_mismatch_count,
            }
        return {
            "type": "tick",
            "devices": devices_out,
            "recording": rec,
            "inference": self._inference_snapshot(),
            "active_session": self.active_session,
            # Native V4 discovery: known sources + subscription state. Empty
            # list when discovery is disabled. Devices that are subscribed and
            # streaming also appear in `devices` above (via their frames); this
            # adds the ones that are known-but-silent plus per-device sub status.
            "discovery": self.discovery.snapshot() if self.discovery else [],
        }

    def _inference_snapshot(self) -> dict:
        """Predicted-LASK output from the FlexGrid -> model pipeline.

        When `openmuscle web --model PATH` is set, this returns live
        predictions from the trained model. The frontend's piston-bar
        renderer auto-detects whether the values are 0..1 (normalized) or
        raw 0..4095 (per the same auto-detect logic that handles LASK5
        ground truth), so any sklearn model that produces consistent units
        will render correctly.
        """
        hand_str = (f"{self.hand_target[0]}:{self.hand_target[1]}"
                    if self.hand_target else None)

        if not self.engine:
            return {
                "available": False,
                "enabled": False,
                "model": None,
                "piston_values": None,
                "status": self.engine_status,
                "hand_target": hand_str,
            }

        if not self.inference_enabled:
            return {
                "available": False,
                "enabled": False,
                "model": self.engine.name,
                "piston_values": None,
                "status": "paused",
                "hand_target": hand_str,
            }

        # Mark as stale if no frame has come through recently (e.g. FlexGrid
        # unplugged) -- the frontend will keep showing the last value but
        # the status line tells the operator data isn't fresh.
        fresh = (
            self._last_inference_values is not None
            and (time.time() - self._last_inference_ts) < 2.0
        )
        last_err = self.engine.last_error
        if last_err:
            status = last_err
        elif fresh:
            status = "live"
        elif self._last_inference_values is not None:
            status = "stale (no flexgrid)"
        else:
            status = "waiting for flexgrid"

        return {
            "available": fresh,
            "enabled": True,
            "model": self.engine.name,
            "piston_values": self._last_inference_values,
            "status": status,
            "hand_target": hand_str,
        }

    # ----- recording -----

    def _auto_pick_sensor(self) -> Optional[str]:
        """First connected matrix-producing device (FlexGrid), if any."""
        for d in self.devices.values():
            if d.device_type == "flexgrid" and d.rows and d.cols:
                return d.device_id
        return None

    # Order in which auto-pick prefers label-producing device types when
    # the operator doesn't pick one explicitly. Quest first because it's
    # the richer ground-truth source -- if both are connected during a
    # comparison session, we want the wider label vector by default.
    AUTO_LABEL_TYPE_PREFERENCE = ("quest_hand", "lask5")

    def _auto_pick_label(self) -> Optional[str]:
        """First connected label-producing device, by type preference."""
        for preferred_type in self.AUTO_LABEL_TYPE_PREFERENCE:
            for d in self.devices.values():
                if d.device_type == preferred_type:
                    return d.device_id
        return None

    # Default match windows per label-device family. Quest WebXR has higher
    # end-to-end latency than LASK5's ESP-NOW path (browser -> WS -> server),
    # so a tighter window would reject too many sensor frames as unpaired.
    DEFAULT_WINDOW_MS_BY_TYPE = {
        "lask5": 100,
        "quest_hand": 175,
    }
    DEFAULT_WINDOW_MS_FALLBACK = 100

    def start_recording(self,
                        sensor_device_id: Optional[str] = None,
                        label_device_id: Optional[str] = None,
                        filename: Optional[str] = None,
                        window_ms: Optional[int] = None,
                        label_count: int = 4,
                        role: str = "left",
                        extra_sensors: Optional[list] = None,
                        with_imu: bool = True,
                        side_labelers: Optional[dict] = None) -> ActiveCapture:
        """Start a paired recording.

        side_labelers (two-hand capture): {"left": device_id, "right": device_id}
        maps each side to its own labeler so the left band is matched to the
        left-hand labeler and the right band to the right-hand labeler. When set,
        the single label_device_id matcher is bypassed for per-side matchers and
        the bands (sensor_device_id + extra_sensors) must be tagged left/right.

        Args:
            sensor_device_id: device producing the FlexGrid matrix. If None,
                              auto-picks the first connected flexgrid device.
            label_device_id:  device producing the labels (LASK5). If None,
                              auto-picks the first connected lask5. Explicitly
                              pass an empty string '' to disable pairing and
                              record sensor frames only (the paired CSV will
                              have no label columns).
            filename: CSV name. JSONL sidecars derived from it.
            window_ms: temporal match window in milliseconds. If None, picked
                       per-device-type from DEFAULT_WINDOW_MS_BY_TYPE
                       (lask5=100, quest_hand=175, else 100).
            label_count: how many label_* columns to write per row (default 4
                         for the standard LASK5 piston count). Ignored when
                         the label device is quest_hand -- in that case the
                         writer infers from the first packet's values length.
        """
        if self.recording is not None:
            raise RuntimeError("Already recording -- stop the current capture first")

        # Normalize the role to the schema-v2 wire vocabulary (lowercase
        # left/right/labeler, board #0091). A single sensor band is left/right;
        # default + warn on anything unexpected so a bad token can't reach the CSV.
        role = (role or "left").strip().lower()
        if role not in ("left", "right", "labeler"):
            self.log_buffer.warn("recording",
                "unknown role '{}', defaulting to 'left' (valid: left/right/labeler)".format(role))
            role = "left"

        # Auto-pick devices if not specified
        if not sensor_device_id:
            sensor_device_id = self._auto_pick_sensor()
            if sensor_device_id is None:
                raise RuntimeError("No flexgrid device seen yet; can't auto-pick sensor source")

        # Bilateral two-hand capture: validate the per-side labelers and use the
        # left labeler as the primary for width/window/sidecar scaffolding. The
        # actual matching is per-side (built below); the single matcher is unused.
        if side_labelers:
            side_labelers = {s: did for s, did in side_labelers.items()
                             if s in ("left", "right") and did}
            if set(side_labelers) != {"left", "right"}:
                raise RuntimeError(
                    "side_labelers needs both 'left' and 'right' device ids")
            for s, did in side_labelers.items():
                if did not in self.devices:
                    raise RuntimeError(f"{s} labeler '{did}' not seen yet")
            label_device_id = side_labelers["left"]   # primary, for scaffolding
        # Distinguish "explicit empty -> sensor-only" from "auto-pick"
        elif label_device_id is None:
            label_device_id = self._auto_pick_label()
            # if still None, sensor-only mode (label_count gets zeroed below)
        elif label_device_id == "":
            label_device_id = None

        sensor_dev = self.devices.get(sensor_device_id)
        if sensor_dev is None:
            raise RuntimeError(f"Sensor device '{sensor_device_id}' not seen yet")
        if sensor_dev.rows == 0 or sensor_dev.cols == 0:
            raise RuntimeError(
                f"Sensor device '{sensor_device_id}' has not sent a matrix payload yet"
            )

        # Build the recorded-bands map {device_id: role}. The primary sensor is
        # the first band; extra_sensors adds more for a multi-band capture. Every
        # band must be a seen flexgrid that has sent a matrix of the SAME dims as
        # the primary (the v2 CSV has one fixed R{r}C{c} feature block, and the
        # trainer's Left||Right concat assumes equal per-band feature width).
        sensors_map = {sensor_device_id: role}
        for entry in (extra_sensors or []):
            if isinstance(entry, dict):
                did, drole = entry.get("device_id"), entry.get("role", "left")
            else:
                did, drole = entry[0], (entry[1] if len(entry) > 1 else "left")
            drole = (drole or "left").strip().lower()
            if drole not in ("left", "right", "labeler"):
                raise RuntimeError(f"Invalid role '{drole}' for band '{did}'")
            if did in sensors_map:
                raise RuntimeError(f"Band '{did}' listed twice")
            band = self.devices.get(did)
            if band is None:
                raise RuntimeError(f"Extra sensor band '{did}' not seen yet")
            if band.rows == 0 or band.cols == 0:
                raise RuntimeError(f"Extra sensor band '{did}' has not sent a matrix yet")
            if (band.rows, band.cols) != (sensor_dev.rows, sensor_dev.cols):
                raise RuntimeError(
                    f"Band '{did}' is {band.rows}x{band.cols} but the primary is "
                    f"{sensor_dev.rows}x{sensor_dev.cols}; all bands must match dims")
            sensors_map[did] = drole

        if label_device_id is not None and label_device_id not in self.devices:
            raise RuntimeError(f"Label device '{label_device_id}' not seen yet")

        effective_label_count = label_count if label_device_id else 0

        # Quest hand tracking sends a wide joint vector whose width depends on
        # the headset / WebXR implementation (Quest 3S = 26 joints * 7 floats =
        # 182 per hand). Rather than hardcode it, pass None so CaptureWriter
        # derives the column count from the first label packet.
        label_dev_for_width = self.devices.get(label_device_id) if label_device_id else None
        label_device_type = label_dev_for_width.device_type if label_dev_for_width else None
        if label_device_type == "quest_hand":
            effective_label_count = None

        # Pick the match window: explicit arg wins; otherwise per-device-type
        # default (Quest needs a wider window than LASK5 because WebXR
        # latency is higher than ESP-NOW).
        if window_ms is None:
            window_ms = self.DEFAULT_WINDOW_MS_BY_TYPE.get(
                label_device_type or "", self.DEFAULT_WINDOW_MS_FALLBACK)

        # Build paths
        name = filename or f"capture_{int(time.time())}.csv"
        if not name.endswith(".csv"):
            name += ".csv"
        name = Path(name).name  # strip any path components the user submitted
        csv_path = self.captures_dir / name
        stem = csv_path.with_suffix("")           # data/raw/merged/foo (no .csv)
        sensor_sidecar = stem.with_suffix(".sensor.jsonl")
        label_sidecar  = stem.with_suffix(".label.jsonl")
        # Labels-schema sidecar is only written for label sources whose
        # column meaning isn't obvious from device_type alone -- v1: Quest.
        labels_schema_sidecar: Optional[Path] = (
            Path(str(stem) + ".labels.schema.json")
            if label_device_type == "quest_hand" else None
        )

        writer = CaptureWriter(
            output_path=str(csv_path),
            matrix_rows=sensor_dev.rows,
            matrix_cols=sensor_dev.cols,
            label_count=effective_label_count,
            schema_version="v2",
            with_imu=with_imu,
        )

        # Open sidecars block-buffered (4 KB). Earlier we used buffering=1
        # (line-buffered) to maximize crash-safety, but at 33 Hz sensor +
        # 25 Hz label that's ~60 syscalls/sec into the disk -- on Windows
        # this is the most likely source of per-packet stalls if Defender /
        # OS journaling hiccups, and a stall here propagates to the WS
        # snapshot lag and looks like "model is slow." Block buffering
        # cuts syscalls ~50x at the cost of losing up to ~4 KB of
        # never-flushed JSONL on a hard crash. We mitigate by flushing
        # every JSONL_FLUSH_EVERY frames in `_record_packet` (~3 s of
        # crash-loss budget instead of 0 s). The paired CSV remains the
        # authoritative training file; JSONL is debug/re-pair only.
        sensor_stream = open(sensor_sidecar, "w", buffering=4096)
        label_stream = open(label_sidecar, "w", buffering=4096) if label_device_id else None

        matcher = TemporalMatcher(window_s=window_ms / 1000.0)

        # Bilateral: one matcher per side + a labeler-id -> side map for routing.
        side_matchers = {}
        label_id_side = {}
        if side_labelers:
            for s, did in side_labelers.items():
                side_matchers[s] = TemporalMatcher(window_s=window_ms / 1000.0)
                label_id_side[did] = s

        self.recording = ActiveCapture(
            writer=writer,
            path=csv_path,
            sensor_device_id=sensor_device_id,
            label_device_id=label_device_id,
            started_at=time.time(),
            rows=sensor_dev.rows,
            cols=sensor_dev.cols,
            window_s=window_ms / 1000.0,
            matcher=matcher,
            role=role,
            sensors=sensors_map,
            sensor_jsonl=sensor_stream,
            label_jsonl=label_stream,
            labels_schema_path=labels_schema_sidecar,
            side_matchers=side_matchers,
            label_id_side=label_id_side,
        )
        self.log_buffer.info("recording",
            "started: {} (sensor={}, label={}, window={}ms)".format(
                name, sensor_device_id, label_device_id or "(none)", window_ms))

        # Seed the meta sidecar with recording context so even a never-
        # annotated capture has device + window provenance. User-facing
        # fields (arm, gesture, notes) stay empty for them to fill in.
        sensor_status = sensor_dev.status if sensor_dev.status else {}
        label_dev_obj = self.devices.get(label_device_id) if label_device_id else None
        label_status = (label_dev_obj.status if (label_dev_obj and label_dev_obj.status) else {})

        auto = {
            "sensor_device_id": sensor_device_id,
            "label_device_id": label_device_id,
            "label_source": label_device_type,   # "lask5" | "quest_hand" | None
            "window_ms": window_ms,
            "sensor_shape": [sensor_dev.rows, sensor_dev.cols],
            # When set, the CSV carries imu_* (sensor band) + lbl_imu_* (matched
            # labeler, e.g. LASK5 gyro for supination) columns after the labels.
            "imu_columns": bool(with_imu),
            "started_at": self.recording.started_at,
            "firmware": {
                "sensor_reset_cause": sensor_status.get("reset_cause_name"),
                "sensor_vbat_at_start": sensor_status.get("vbat"),
            },
        }
        # Auto-link to active session if one is open. Also seed the user-
        # editable arm/subject fields from the session so the operator
        # doesn't have to retype them per capture (they can still override
        # via the per-capture meta editor).
        seed_user = {}
        if self.active_session is not None:
            sid = self.active_session.get("id")
            auto["session_id"] = sid
            auto["session_name"] = self.active_session.get("name")
            if self.active_session.get("arm"):
                seed_user["arm"] = self.active_session["arm"]
            if self.active_session.get("subject"):
                seed_user["subject"] = self.active_session["subject"]
            # Tag with the session id so capture-search/filter UIs can find it
            session_tag = "session:" + str(sid)
            seed_user["tags"] = (self.active_session.get("tags") or []) + [session_tag]

        # Schema-v2 interop metadata at the TOP LEVEL, matching the phone
        # meta.json keys (board #0097) so phone- and PC-captured sessions carry
        # identical metadata. label_source uses the phone wire vocabulary
        # (lask5 / quest / manual); roles maps device_id -> role token. mirror is
        # False here (one-limb mirroring is a multi-band concern, PROTOCOL.md 8.5).
        # The PC-specific provenance stays under .auto (which keeps its own
        # label_source = the raw device_type).
        label_source_wire = {"lask5": "lask5", "quest_hand": "quest"}.get(
            label_device_type, label_device_type)
        interop_meta = {
            "schema": "v2",
            "mirror": False,
            "label_source": label_source_wire,
            "roles": dict(sensors_map),   # {device_id: role} for every band
            "created_ms": int(self.recording.started_at * 1000),
        }

        # write_capture_meta routes 'auto' into the .auto sub-dict and
        # user-fields (arm, subject, tags) + the interop keys into their
        # top-level slots -- so a single call seeds everything correctly.
        try:
            self.write_capture_meta(name, {"auto": auto, **interop_meta, **seed_user})
        except Exception as e:
            self.log_buffer.warn("meta", "could not seed meta for {}: {}".format(name, e))

        # Add this capture to the session's captures list and bump its
        # capture_count so the Sessions panel reflects it live.
        if self.active_session is not None:
            self.link_capture_to_session(self.active_session["id"], name)

        return self.recording

    def start_multiband_recording(self, filename: Optional[str] = None,
                                  window_ms: Optional[int] = None) -> ActiveCapture:
        """Start a multi-band capture from the role tags set in the Sources panel.

        Gathers every flexgrid tagged left/right as a band and the device tagged
        labeler (if any, else auto-pick) as the label source, then delegates to
        start_recording. The operator tags sources once in the Sources panel and
        records with one click.
        """
        if not self.discovery:
            raise RuntimeError("discovery is disabled; can't gather role tags")
        snap = self.discovery.snapshot()
        bands = [(d["device_id"], d.get("role"))
                 for d in snap
                 if d.get("device_type") == "flexgrid"
                 and d.get("role") in ("left", "right")]
        if not bands:
            raise RuntimeError(
                "no flexgrid bands tagged left/right; tag sources in the "
                "Sources panel first")
        # Deterministic order: left band(s) before right, so the trainer's
        # Left||Right concat is stable regardless of discovery iteration order.
        bands.sort(key=lambda b: (b[1] != "left", b[0]))
        labeler = next((d["device_id"] for d in snap
                        if d.get("role") == "labeler"), None)

        primary_id, primary_role = bands[0]
        extras = [{"device_id": did, "role": role} for did, role in bands[1:]]
        return self.start_recording(
            sensor_device_id=primary_id, role=primary_role,
            extra_sensors=extras,
            label_device_id=labeler,   # None -> start_recording auto-picks
            filename=filename, window_ms=window_ms)

    def start_bilateral_recording(self, filename: Optional[str] = None,
                                  window_ms: Optional[int] = None) -> ActiveCapture:
        """Two-hand capture: the left band is matched to the left-hand VR stream
        and the right band to the right-hand stream.

        Bands come from the Sources-panel role tags (one flexgrid tagged left, one
        tagged right). The labelers are the two Quest hand streams the VR client
        sends when opened with ?arm=both: device_ids quest-left / quest-right.
        """
        if not self.discovery:
            raise RuntimeError("discovery is disabled; can't gather role tags")
        snap = self.discovery.snapshot()
        by_side = {d.get("role"): d["device_id"] for d in snap
                   if d.get("device_type") == "flexgrid"
                   and d.get("role") in ("left", "right")}
        if set(by_side) != {"left", "right"}:
            raise RuntimeError(
                "two-hand capture needs one flexgrid tagged 'left' AND one tagged "
                "'right' in the Sources panel")
        side_labelers = {}
        for side in ("left", "right"):
            did = f"quest-{side}"
            dev = self.devices.get(did)
            if dev is not None and dev.device_type == "quest_hand":
                side_labelers[side] = did
        if set(side_labelers) != {"left", "right"}:
            raise RuntimeError(
                "two-hand capture needs both quest-left and quest-right streaming; "
                "open the VR app with ?arm=both")
        return self.start_recording(
            filename=filename, window_ms=window_ms,
            sensor_device_id=by_side["left"], role="left",
            extra_sensors=[{"device_id": by_side["right"], "role": "right"}],
            side_labelers=side_labelers)

    def stop_recording(self) -> Optional[dict]:
        if self.recording is None:
            return None
        rec = self.recording
        rec.writer.close()
        for stream in (rec.sensor_jsonl, rec.label_jsonl):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        self.log_buffer.info("recording",
            "stopped: {} -- {} matched / {} sensor frames ({}%), {}s".format(
                rec.path.name, rec.matched_count, rec.sensor_frames_seen,
                round(rec.match_rate * 100, 1), round(rec.duration_s, 1)))
        # If any rows had to be padded/truncated to the locked label width,
        # call it out -- it means the label source sent variable-length
        # frames (e.g. partial hand tracking), and those rows have some
        # zero-filled joint columns.
        if rec.label_width_mismatch_count:
            self.log_buffer.warn("recording",
                "{}: {} row(s) padded/truncated to locked label width {} "
                "(variable-length label frames -- some joint columns are "
                "zero-filled)".format(
                    rec.path.name, rec.label_width_mismatch_count,
                    rec.locked_label_count))
        result = {
            "filename": rec.path.name,
            "rows": rec.row_count,
            "duration_s": round(rec.duration_s, 1),
            "path": str(rec.path),
            "sensor_device_id": rec.sensor_device_id,
            "label_device_id": rec.label_device_id,
            "window_ms": int(rec.window_s * 1000),
            "matched": rec.matched_count,
            "unpaired_sensor": rec.unpaired_sensor_count,
            "sensor_frames_seen": rec.sensor_frames_seen,
            "label_packets_seen": rec.label_packets_seen,
            "label_width_mismatch": rec.label_width_mismatch_count,
            "match_rate": round(rec.match_rate, 3),
            "sidecars": {
                "sensor": str(rec.path.with_suffix("")) + ".sensor.jsonl",
                "label": (str(rec.path.with_suffix("")) + ".label.jsonl") if rec.label_jsonl else None,
                "labels_schema": (str(rec.labels_schema_path)
                                  if (rec.labels_schema_path and rec.labels_schema_written)
                                  else None),
            },
        }
        self.recording = None
        return result

    # ----- captures listing -----

    def list_captures(self) -> list[dict]:
        out = []
        if not self.captures_dir.exists():
            return out
        for p in sorted(self.captures_dir.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True):
            stat = p.stat()
            meta = self.read_capture_meta(p.name)
            # Compact summary fields for the table view; full meta is fetched
            # via /api/captures/{name}/meta when the user clicks edit.
            meta_summary = None
            if meta:
                meta_summary = {
                    "arm": meta.get("arm"),
                    "subject": meta.get("subject") or None,
                    "gesture": meta.get("gesture") or None,
                    "tags": meta.get("tags") or [],
                    "has_notes": bool(meta.get("notes")),
                }
            out.append({
                "name": p.name,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
                "meta": meta_summary,
            })
        return out

    def capture_path(self, name: str) -> Path | None:
        # Whitelist: only return paths inside captures_dir
        name = Path(name).name
        p = self.captures_dir / name
        if not p.exists() or p.suffix != ".csv":
            return None
        return p

    def delete_capture(self, name: str) -> bool:
        p = self.capture_path(name)
        if p is None:
            return False
        # If this capture was part of a session, unlink it there too
        meta = self.read_capture_meta(name)
        sid = (meta.get("auto") or {}).get("session_id") if meta else None
        if sid:
            try:
                self.unlink_capture_from_session(sid, name)
            except Exception:
                pass
        p.unlink()
        # Also delete sidecars if present
        stem = p.with_suffix("")
        for suffix in (".sensor.jsonl", ".label.jsonl", ".meta.json", ".labels.schema.json"):
            sidecar = Path(str(stem) + suffix)
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass
        return True

    # ----- sessions -----
    #
    # A session is a logical grouping of captures under one set of operator-
    # level metadata (subject, arm, gesture set, notes). Storage is a JSON
    # file per session at `<captures_dir>/../sessions/<id>.json`. Captures
    # themselves stay flat in captures_dir; their .meta.json grows a
    # `auto.session_id` field linking back. The training pipeline doesn't
    # need to know about sessions -- it just consumes capture CSVs as
    # before. This keeps the abstraction additive.
    #
    # There is at most ONE active session at a time, mirroring the
    # already-existing "one recording at a time" invariant. Recordings
    # made while a session is active get auto-tagged with the session id.

    @property
    def sessions_dir(self) -> Path:
        d = self.captures_dir.parent / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _session_path(self, session_id: str) -> Optional[Path]:
        """Whitelist-safe path inside sessions_dir."""
        if not session_id:
            return None
        clean = Path(session_id).name  # strip any '..' / '/'
        if not clean or clean.startswith("."):
            return None
        return self.sessions_dir / (clean + ".json")

    def _new_session_id(self) -> str:
        """Time-based id like '2026-05-16T08-42-13'. Local time so it's
        readable on the filesystem; never collides at 1-second granularity."""
        return time.strftime("%Y-%m-%dT%H-%M-%S", time.localtime())

    def list_sessions(self) -> list:
        """All sessions, newest first. Cheap: just reads the JSONs."""
        out = []
        if not self.sessions_dir.exists():
            return out
        for p in sorted(self.sessions_dir.glob("*.json"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                with open(p, "r") as f:
                    s = json.load(f)
                # Inject filesystem hints
                s["_path"] = p.name
                out.append(s)
            except Exception:
                continue
        return out

    def get_session(self, session_id: str) -> Optional[dict]:
        p = self._session_path(session_id)
        if p is None or not p.exists():
            return None
        try:
            with open(p, "r") as f:
                return json.load(f)
        except Exception:
            return None

    def _write_session(self, session: dict) -> None:
        p = self._session_path(session["id"])
        if p is None:
            raise RuntimeError(f"Invalid session id: {session.get('id')!r}")
        with open(p, "w") as f:
            json.dump(session, f, indent=2)

    def start_session(self, name: str = "", subject: str = "", arm: Optional[str] = None,
                      gestures: Optional[list] = None, notes: str = "",
                      tags: Optional[list] = None) -> dict:
        """Start a new session and mark it active.

        Refuses if another session is already active -- the operator should
        explicitly end the prior one. (We could allow stacked sessions but
        it's the kind of subtle UX bug that produces orphaned metadata.)
        """
        if self.active_session is not None:
            raise RuntimeError(
                "Another session is already active: {}. End it first.".format(
                    self.active_session.get("id")))
        sid = self._new_session_id()
        now = time.time()
        session = {
            "id": sid,
            "name": name or sid,
            "subject": subject or "",
            "arm": arm if arm in ("left", "right") else None,
            "gestures": list(gestures or []),
            "tags": list(tags or []),
            "notes": notes or "",
            "started_at": now,
            "ended_at": None,
            "captures": [],
            "capture_count": 0,
        }
        self._write_session(session)
        self.active_session = session
        self.log_buffer.info("session",
            "started: {} (subject={}, arm={})".format(
                sid, subject or "-", arm or "-"))
        return session

    def end_session(self) -> Optional[dict]:
        """Stamp ended_at on the active session and clear active state."""
        if self.active_session is None:
            return None
        sid = self.active_session["id"]
        # Re-read from disk in case someone updated meta concurrently
        session = self.get_session(sid) or self.active_session
        session["ended_at"] = time.time()
        self._write_session(session)
        self.log_buffer.info("session",
            "ended: {} ({} captures, {:.0f}s)".format(
                sid, session.get("capture_count", 0),
                (session["ended_at"] - session.get("started_at", session["ended_at"]))))
        self.active_session = None
        return session

    def update_session(self, session_id: str, partial: dict) -> Optional[dict]:
        """Merge-update a session's metadata. Protected fields (id, started_at,
        ended_at, captures, capture_count) are not overwritten."""
        s = self.get_session(session_id)
        if s is None:
            return None
        for k, v in (partial or {}).items():
            if k in ("id", "started_at", "ended_at", "captures", "capture_count"):
                continue
            if k == "arm" and v not in ("left", "right", None, ""):
                continue
            s[k] = v
        s["modified_at"] = time.time()
        self._write_session(s)
        # If this is the active session, keep our in-memory ref in sync
        if self.active_session is not None and self.active_session.get("id") == session_id:
            self.active_session = s
        self.log_buffer.info("session", "updated: {}".format(session_id))
        return s

    def delete_session(self, session_id: str, also_unlink_captures: bool = True) -> bool:
        p = self._session_path(session_id)
        if p is None or not p.exists():
            return False
        if also_unlink_captures:
            # Strip session_id from every capture that pointed here. Don't
            # delete the captures themselves -- the data is the asset.
            s = self.get_session(session_id) or {}
            for name in s.get("captures", []):
                try:
                    self.write_capture_meta(name, {"auto": {"session_id": None}})
                except Exception:
                    pass
        p.unlink()
        if self.active_session is not None and self.active_session.get("id") == session_id:
            self.active_session = None
        self.log_buffer.info("session", "deleted: {}".format(session_id))
        return True

    def link_capture_to_session(self, session_id: str, capture_name: str) -> Optional[dict]:
        """Add a capture to a session's `captures` list (idempotent) and
        bump capture_count. Returns the updated session, or None if the
        session doesn't exist."""
        s = self.get_session(session_id)
        if s is None:
            return None
        if capture_name not in s.get("captures", []):
            s.setdefault("captures", []).append(capture_name)
            s["capture_count"] = len(s["captures"])
            self._write_session(s)
            if self.active_session is not None and self.active_session.get("id") == session_id:
                self.active_session = s
        return s

    def unlink_capture_from_session(self, session_id: str, capture_name: str) -> Optional[dict]:
        s = self.get_session(session_id)
        if s is None:
            return None
        if capture_name in s.get("captures", []):
            s["captures"].remove(capture_name)
            s["capture_count"] = len(s["captures"])
            self._write_session(s)
            if self.active_session is not None and self.active_session.get("id") == session_id:
                self.active_session = s
        return s

    # ----- capture metadata sidecars -----

    # Top-level keys we expose for editing through the UI. Anything else the
    # user PUTs lands under `extras` so the JSON stays self-describing but
    # doesn't accidentally collide with reserved fields.
    META_USER_KEYS = ("arm", "subject", "gesture", "tags", "notes")
    # Machine-set schema-v2 interop keys, written at the TOP LEVEL (not under
    # extras) so they match the phone meta.json shape byte-for-key (board #0097):
    # the trainer reads the same keys whether a session was captured on phone or PC.
    META_INTEROP_KEYS = ("schema", "mirror", "label_source", "roles", "created_ms")

    def _meta_path(self, csv_name: str) -> Optional[Path]:
        """Sidecar path for a capture's metadata JSON. Whitelist-guarded so
        the caller can only address files inside captures_dir."""
        p = self.capture_path(csv_name)
        if p is None:
            return None
        return Path(str(p.with_suffix("")) + ".meta.json")

    def read_capture_meta(self, name: str) -> dict:
        """Read a capture's metadata sidecar. Returns {} if absent."""
        path = self._meta_path(name)
        if path is None or not path.exists():
            return {}
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def write_capture_meta(self, name: str, partial: dict) -> dict:
        """Merge `partial` into the capture's metadata sidecar and persist.

        Existing keys are overwritten; missing ones are kept. The `auto`
        sub-dict (machine-set, recording-context fields) is merge-updated
        rather than replaced -- so a UI edit that only touches user fields
        leaves auto.* alone, and vice versa.

        Bumps `modified_at` automatically. Returns the full merged dict.
        """
        path = self._meta_path(name)
        if path is None:
            raise RuntimeError(f"Capture not found: {name}")

        existing = self.read_capture_meta(name)

        # Init scaffolding on first write
        if not existing:
            existing = {
                "arm": None,
                "subject": "",
                "gesture": "",
                "tags": [],
                "notes": "",
                "created_at": time.time(),
                "auto": {},
            }

        # Merge the user-editable keys directly. `auto` and `extras` get a
        # deep-ish merge so partial updates don't nuke whole sub-dicts.
        for k, v in (partial or {}).items():
            if k == "auto" and isinstance(v, dict):
                if not isinstance(existing.get("auto"), dict):
                    existing["auto"] = {}
                existing["auto"].update(v)
            elif k == "created_at":
                # Don't let clients rewrite this
                continue
            elif k in self.META_USER_KEYS or k in self.META_INTEROP_KEYS:
                existing[k] = v
            else:
                # Land unknown keys under `extras` so user can attach arbitrary
                # structured data without colliding with reserved fields.
                if not isinstance(existing.get("extras"), dict):
                    existing["extras"] = {}
                existing["extras"][k] = v

        existing["modified_at"] = time.time()

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(existing, f, indent=2)

        self.log_buffer.info("meta",
            "updated {} ({})".format(
                name,
                ", ".join("{}={}".format(k, partial.get(k)) for k in (partial or {})
                          if k in self.META_USER_KEYS) or "auto-only"))

        return existing

    def _seed_capture_meta(self, name: str, auto_dict: dict) -> None:
        """Called from start_recording to write the recording-context meta.
        Won't overwrite user-edits if a sidecar already exists (shouldn't
        be the case, but be defensive)."""
        try:
            self.write_capture_meta(name, {"auto": auto_dict})
        except Exception as e:
            self.log_buffer.warn("meta", "could not seed meta for {}: {}".format(name, e))

    # ----- training + model management -----

    def train_from_captures(self,
                            capture_names: list,
                            model_type: str = "random_forest",
                            n_estimators: int = 100,
                            test_split: float = 0.2,
                            activate: bool = True) -> dict:
        """Combine N captures from captures_dir, train a model, optionally
        hot-swap it into the inference engine.

        Args:
            capture_names: list of CSV filenames (NOT full paths) inside
                           captures_dir. Each is whitelisted via
                           capture_path() before use.
            model_type:    'random_forest' for now.
            n_estimators:  RF n_estimators (default 100).
            test_split:    fraction held out for evaluation.
            activate:      if True, the trained model is loaded into
                           self.engine immediately, so live inference uses
                           it without restarting the server.

        Returns:
            {model_path, metrics: {r2, mse, ...}, active: bool}

        Raises:
            RuntimeError on invalid capture or empty result.
        """
        # Import here to keep the FastAPI cold-start fast.
        import tempfile
        import os
        from openmuscle.ml.training import train_model
        from openmuscle.data.converter import combine_csvs

        if not capture_names:
            raise RuntimeError("No captures selected")
        self.log_buffer.info("training",
            "started: {} captures, model={}, trees={}, test_split={}".format(
                len(capture_names), model_type, n_estimators, test_split))

        paths = []
        for name in capture_names:
            p = self.capture_path(name)
            if p is None:
                raise RuntimeError(f"Capture not found: {name}")
            paths.append(str(p))

        # Combine (or just use the single path directly)
        if len(paths) == 1:
            combined_path = paths[0]
            cleanup = None
        else:
            fd, combined_path = tempfile.mkstemp(prefix="om_combined_", suffix=".csv")
            os.close(fd)
            cleanup = combined_path
            combine_csvs(paths, combined_path)

        try:
            model, metrics = train_model(
                data_path=combined_path,
                model_type=model_type,
                n_estimators=n_estimators,
                test_split=test_split,
            )
        finally:
            if cleanup:
                try:
                    os.unlink(cleanup)
                except OSError:
                    pass

        # train_model() saved the model via ModelRegistry; find the most-
        # recent registry dir (timestamp suffix monotonic) to get its path.
        model_path = self._latest_registered_model_path()

        activated = False
        if activate and model_path:
            try:
                self.set_model(model_path)
                activated = True
            except Exception as e:
                # Don't fail the whole training response if hot-swap fails;
                # the file is on disk, the operator can still load it.
                self.engine_status = "trained but activate failed: {}".format(e)
                self.log_buffer.error("training", "load-after-train failed: {}".format(e))

        self.log_buffer.info("training",
            "done: rows={} R²={:.3f} MSE={:.4f} feats={}x{} model={} ({})".format(
                metrics.get("n_train", 0) + metrics.get("n_test", 0),
                metrics.get("r2", 0.0),
                metrics.get("mse", 0.0),
                metrics.get("n_features", 0),
                metrics.get("n_labels", 0),
                Path(model_path).parent.name if model_path else "?",
                "loaded · click ▶ to run" if activated else "saved only"))

        return {
            "model_path": model_path,
            "metrics": metrics,
            "active": activated,
            "captures": list(capture_names),
        }

    def _latest_registered_model_path(self) -> Optional[str]:
        """Find the most recent model.pkl under data/models/."""
        try:
            from openmuscle.ml.registry import ModelRegistry
            reg = ModelRegistry()
            entries = reg.list_models()
            if not entries:
                return None
            # list_models returns ordered by directory name (timestamped),
            # which is chronological. Last entry == newest.
            return entries[-1].get("path")
        except Exception:
            return None

    def set_model(self, model_path: str) -> None:
        """Hot-swap the inference engine. Idempotent: same path is a no-op
        unless the existing engine has a different one.

        Loading a model does NOT start running it. Inference stays paused
        until the operator clicks ▶ Resume (or POSTs /api/inference/enabled
        {enabled: true}). This was a deliberate change from the earlier
        "auto-start on load" behaviour, which surprised the operator by
        having the model start consuming CPU + driving the hand the moment
        a different model was clicked in the Models panel. Explicit on/off
        beats clever defaults here.

        Exception: the `--model` CLI flag at server startup DOES auto-start
        (handled in __init__), since passing a model on the command line is
        an explicit "run this now" intent.
        """
        from openmuscle.web.inference import InferenceEngine
        if self.engine is not None and str(self.engine.model_path) == str(model_path):
            return  # already loaded; don't touch the enabled flag
        new_engine = InferenceEngine(model_path)
        self.engine = new_engine
        self.engine_status = "loaded"
        # Drop the cached prediction -- it's from the OLD model, not relevant.
        self._last_inference_values = None
        self._last_inference_ts = 0.0
        # NB: deliberately do NOT touch self.inference_enabled here. Loading
        # a new model preserves whatever paused/running state the operator
        # had. If you want it running, click ▶.
        self.log_buffer.info("inference", "model loaded: {} (inference still {})".format(
            new_engine.name,
            "running" if self.inference_enabled else "PAUSED -- click ▶ to start"))

    def set_inference_enabled(self, enabled: bool) -> None:
        """Toggle inference on/off. Doesn't unload the model -- pausing is
        a soft state so the operator can resume without reloading."""
        prev = self.inference_enabled
        self.inference_enabled = bool(enabled)
        if prev != self.inference_enabled:
            self.log_buffer.info("inference",
                "{} (model={})".format(
                    "resumed" if self.inference_enabled else "paused",
                    self.engine.name if self.engine else "none"))
        if not self.inference_enabled:
            # Clear the cached prediction so the panel shows "paused" cleanly
            # rather than freezing on the last value.
            self._last_inference_values = None
            self._last_inference_ts = 0.0

    def set_hand_target(self, host: Optional[str], port: int = 3145) -> None:
        """Set or clear the robot-hand UDP forwarding target. Pass host=None
        (or empty string) to disable forwarding."""
        if not host:
            self.hand_target = None
            self.log_buffer.info("inference", "hand forwarding disabled")
        else:
            try:
                port = int(port)
            except Exception:
                raise RuntimeError(f"Invalid port: {port!r}")
            if not (1 <= port <= 65535):
                raise RuntimeError(f"Port out of range: {port}")
            self.hand_target = (host.strip(), port)
            self.log_buffer.info("inference",
                "hand target set: {}:{}".format(self.hand_target[0], self.hand_target[1]))
        # Drop the cached socket so the next sendto() reopens with whatever
        # bindings the OS gives it for the new target.
        if self._hand_sock is not None:
            try:
                self._hand_sock.close()
            except Exception:
                pass
            self._hand_sock = None

    def list_models(self) -> list:
        """List models in the registry, augmented with `active` flag."""
        from openmuscle.ml.registry import ModelRegistry
        reg = ModelRegistry()
        out = []
        active_path = str(self.engine.model_path) if self.engine else None
        for m in reg.list_models():
            entry = dict(m)
            entry["active"] = (entry.get("path") == active_path)
            out.append(entry)
        # Newest first in the UI
        out.reverse()
        return out

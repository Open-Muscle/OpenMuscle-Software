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
from openmuscle.protocol.schema import OpenMusclePacket
from openmuscle.receiver.matcher import TemporalMatcher
from openmuscle.receiver.udp_listener import UDPListener
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
    sensor_jsonl: Optional[IO] = None
    label_jsonl: Optional[IO] = None
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
                 hand_target: Optional[tuple] = None):
        """
        Args:
            udp_port: incoming device telemetry port (FlexGrid + LASK5)
            captures_dir: where recorded CSVs go
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

        self.listener = UDPListener(port=udp_port)
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
        self._started = True

    def stop(self):
        self.listener.stop()
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

    # Flush JSONL sidecars every N frames to bound crash-loss to ~3 s of
    # data while keeping syscalls ~50× cheaper than line-buffered writes.
    # At 33 Hz sensor + 25 Hz label rates, 100 ≈ 3 s.
    JSONL_FLUSH_EVERY = 100

    def _record_packet(self, pkt: OpenMusclePacket):
        """Route a packet to the active capture: sidecars + matcher + paired CSV."""
        rec = self.recording
        if rec is None:
            return

        # --- Label stream: append to matcher and JSONL sidecar ---
        if rec.label_device_id is not None and pkt.device_id == rec.label_device_id:
            rec.matcher.add_label(pkt)
            rec.label_packets_seen += 1
            self._write_jsonl(rec.label_jsonl, pkt)
            # Bounded crash-loss flush
            if (rec.label_packets_seen % self.JSONL_FLUSH_EVERY == 0
                    and rec.label_jsonl is not None):
                try:
                    rec.label_jsonl.flush()
                except Exception:
                    pass
            return

        # --- Sensor stream: write JSONL sidecar always, paired CSV when matched ---
        if pkt.device_id != rec.sensor_device_id:
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
        if rec.label_device_id is None:
            # Sensor-only mode (no label device): just write sensor + empty labels.
            label_values = []
        else:
            matched = rec.matcher.match(pkt)
            if matched is None:
                rec.unpaired_sensor_count += 1
                return  # drop unpaired sensor frames from the paired CSV
            rec.matched_count += 1
            label_values = list(matched.data.get("values", []))

        # Flatten as-sent [cols][rows] matrix row-major. Header in CaptureWriter
        # is R0C0..R0Cn, R1C0.., so iterating rows-then-cols here keeps the
        # column meaning correct (cf. the col-major bug we fixed in 245cb8f).
        rows = len(mat[0])
        cols = len(mat)
        flat = [mat[c][r] for r in range(rows) for c in range(cols)]
        rec.writer.write_row(pkt.receive_time, flat, label_values)

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

        Builds 5 servo angles in 0..179 from the 4 piston predictions (assumed
        normalized 0..1; clamped) plus the most recent LASK5 joystick X as the
        5th. The hand's `'PC'` device config uses linear 0..179 -> 0..179
        mapping, so values land directly on servo angles.
        """
        # Pistons -> 0..179, assuming model output is normalized 0..1.
        # Anything else gets clamped, which is the right failure mode --
        # bracelet finger goes to extreme rather than 4000-degree angle.
        angles = []
        for v in pred[:4]:
            try:
                v = max(0.0, min(1.0, float(v)))
            except Exception:
                v = 0.0
            angles.append(int(v * 179))

        # 5th finger = joystick X from the most recent LASK5 packet. Range
        # 0..4095 -> 0..179. Default to center (90) if no LASK5 has been seen.
        joy_x = None
        for d in self.devices.values():
            if d.device_type == "lask5" and d.last_joystick:
                jx = d.last_joystick.get("x")
                if isinstance(jx, (int, float)):
                    joy_x = jx
                    break
        if joy_x is None:
            angles.append(90)
        else:
            angles.append(max(0, min(179, int((joy_x / 4095.0) * 179))))

        # Build the CSV the hand expects: 'PC,a1,a2,a3,a4,a5'
        payload = ("PC," + ",".join(str(a) for a in angles)).encode("utf-8")

        try:
            if self._hand_sock is None:
                self._hand_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._hand_sock.setblocking(False)
            self._hand_sock.sendto(payload, self.hand_target)
        except Exception:
            # Non-fatal: hand might be offline / on a different subnet.
            # We don't spam logs since this fires per FlexGrid packet.
            pass

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
            }
        return {
            "type": "tick",
            "devices": devices_out,
            "recording": rec,
            "inference": self._inference_snapshot(),
            "active_session": self.active_session,
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

    def _auto_pick_label(self) -> Optional[str]:
        """First connected label-producing device (LASK5), if any."""
        for d in self.devices.values():
            if d.device_type == "lask5":
                return d.device_id
        return None

    def start_recording(self,
                        sensor_device_id: Optional[str] = None,
                        label_device_id: Optional[str] = None,
                        filename: Optional[str] = None,
                        window_ms: int = 100,
                        label_count: int = 4) -> ActiveCapture:
        """Start a paired recording.

        Args:
            sensor_device_id: device producing the FlexGrid matrix. If None,
                              auto-picks the first connected flexgrid device.
            label_device_id:  device producing the labels (LASK5). If None,
                              auto-picks the first connected lask5. Explicitly
                              pass an empty string '' to disable pairing and
                              record sensor frames only (the paired CSV will
                              have no label columns).
            filename: CSV name. JSONL sidecars derived from it.
            window_ms: temporal match window in milliseconds (default 100).
            label_count: how many label_* columns to write per row (default 4
                         for the standard LASK5 piston count).
        """
        if self.recording is not None:
            raise RuntimeError("Already recording -- stop the current capture first")

        # Auto-pick devices if not specified
        if not sensor_device_id:
            sensor_device_id = self._auto_pick_sensor()
            if sensor_device_id is None:
                raise RuntimeError("No flexgrid device seen yet; can't auto-pick sensor source")

        # Distinguish "explicit empty -> sensor-only" from "auto-pick"
        if label_device_id is None:
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

        if label_device_id is not None and label_device_id not in self.devices:
            raise RuntimeError(f"Label device '{label_device_id}' not seen yet")

        effective_label_count = label_count if label_device_id else 0

        # Build paths
        name = filename or f"capture_{int(time.time())}.csv"
        if not name.endswith(".csv"):
            name += ".csv"
        name = Path(name).name  # strip any path components the user submitted
        csv_path = self.captures_dir / name
        stem = csv_path.with_suffix("")           # data/raw/merged/foo (no .csv)
        sensor_sidecar = stem.with_suffix(".sensor.jsonl")
        label_sidecar  = stem.with_suffix(".label.jsonl")

        writer = CaptureWriter(
            output_path=str(csv_path),
            matrix_rows=sensor_dev.rows,
            matrix_cols=sensor_dev.cols,
            label_count=effective_label_count,
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
            sensor_jsonl=sensor_stream,
            label_jsonl=label_stream,
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
            "window_ms": window_ms,
            "sensor_shape": [sensor_dev.rows, sensor_dev.cols],
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

        # write_capture_meta routes 'auto' into the .auto sub-dict and
        # user-fields (arm, subject, tags) into their top-level slots --
        # so a single call seeds everything correctly.
        try:
            self.write_capture_meta(name, {"auto": auto, **seed_user})
        except Exception as e:
            self.log_buffer.warn("meta", "could not seed meta for {}: {}".format(name, e))

        # Add this capture to the session's captures list and bump its
        # capture_count so the Sessions panel reflects it live.
        if self.active_session is not None:
            self.link_capture_to_session(self.active_session["id"], name)

        return self.recording

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
            "match_rate": round(rec.match_rate, 3),
            "sidecars": {
                "sensor": str(rec.path.with_suffix("")) + ".sensor.jsonl",
                "label": (str(rec.path.with_suffix("")) + ".label.jsonl") if rec.label_jsonl else None,
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
        for suffix in (".sensor.jsonl", ".label.jsonl", ".meta.json"):
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
            elif k in self.META_USER_KEYS:
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

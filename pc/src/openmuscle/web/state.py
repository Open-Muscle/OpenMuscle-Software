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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import socket

from openmuscle.data.storage import CaptureWriter
from openmuscle.protocol.schema import OpenMusclePacket
from openmuscle.receiver.udp_listener import UDPListener
from openmuscle.web.inference import InferenceEngine


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
    writer: CaptureWriter
    path: Path
    device_id: str
    started_at: float
    rows: int
    cols: int

    @property
    def row_count(self) -> int:
        return self.writer.row_count

    @property
    def duration_s(self) -> float:
        return time.time() - self.started_at


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

        self.listener = UDPListener(port=udp_port)
        self.devices: dict[str, DeviceInfo] = {}
        self.recording: Optional[ActiveCapture] = None
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
            except Exception as e:
                # Don't crash the server on bad model; show the error in the UI.
                self.engine_status = "load failed: {}".format(e)

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

        # Write to active capture if recording and shape matches.
        # CaptureWriter emits row-major column headers (R0C0, R0C1, ..., R0Cn,
        # R1C0, ...), so we have to flatten the as-sent [cols][rows] matrix
        # in row-major order. Previously we wrote it col-major which made
        # "R0C1" actually contain the value for (col=0, row=1) -- broke
        # every downstream analysis script.
        if self.recording and self.recording.device_id == pkt.device_id:
            mat = pkt.data.get("matrix")
            if mat:
                rows = len(mat[0])
                cols = len(mat)
                flat = [mat[c][r] for r in range(rows) for c in range(cols)]
                self.recording.writer.write_row(pkt.receive_time, flat, [])

        # Run inference on flexgrid frames if an engine is loaded. We do this
        # synchronously in the same thread as packet handling -- RF predict is
        # ~ms, well under the 40ms inter-frame budget at 25Hz.
        if self.engine and pkt.device_type == "flexgrid":
            mat = pkt.data.get("matrix")
            if mat:
                pred = self.engine.predict(mat)
                if pred is not None:
                    self._last_inference_values = pred
                    self._last_inference_ts = time.time()
                    if self.hand_target:
                        self._forward_to_hand(pred)

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
            })
        rec = None
        if self.recording:
            rec = {
                "filename": self.recording.path.name,
                "device_id": self.recording.device_id,
                "rows": self.recording.row_count,
                "duration_s": round(self.recording.duration_s, 1),
                "shape": [self.recording.rows, self.recording.cols],
            }
        return {
            "type": "tick",
            "devices": devices_out,
            "recording": rec,
            "inference": self._inference_snapshot(),
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
        if not self.engine:
            return {
                "available": False,
                "model": None,
                "piston_values": None,
                "status": self.engine_status,
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
            "model": self.engine.name,
            "piston_values": self._last_inference_values,
            "status": status,
        }

    # ----- recording -----

    def start_recording(self, device_id: str, filename: str | None = None) -> ActiveCapture:
        if self.recording is not None:
            raise RuntimeError("Already recording — stop the current capture first")
        dev = self.devices.get(device_id)
        if dev is None:
            raise RuntimeError(f"Device '{device_id}' not seen yet")
        if dev.rows == 0 or dev.cols == 0:
            raise RuntimeError(f"Device '{device_id}' has not sent a matrix payload yet")

        name = filename or f"capture_{int(time.time())}.csv"
        if not name.endswith(".csv"):
            name += ".csv"
        # Strip any path components a user might submit
        name = Path(name).name
        path = self.captures_dir / name

        writer = CaptureWriter(
            output_path=str(path),
            matrix_rows=dev.rows,
            matrix_cols=dev.cols,
            label_count=0,
        )
        self.recording = ActiveCapture(
            writer=writer,
            path=path,
            device_id=device_id,
            started_at=time.time(),
            rows=dev.rows,
            cols=dev.cols,
        )
        return self.recording

    def stop_recording(self) -> Optional[dict]:
        if self.recording is None:
            return None
        rec = self.recording
        rec.writer.close()
        result = {
            "filename": rec.path.name,
            "rows": rec.row_count,
            "duration_s": round(rec.duration_s, 1),
            "path": str(rec.path),
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
            out.append({
                "name": p.name,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
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
        p.unlink()
        return True

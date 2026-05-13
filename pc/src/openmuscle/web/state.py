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

from openmuscle.data.storage import CaptureWriter
from openmuscle.protocol.schema import OpenMusclePacket
from openmuscle.receiver.udp_listener import UDPListener


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
    last_matrix: list = field(default_factory=list)   # [cols][rows] as-sent

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

    def __init__(self, udp_port: int = 3141, captures_dir: Path | None = None):
        self.udp_port = udp_port
        self.captures_dir = Path(captures_dir or Path("data/raw/merged"))
        self.captures_dir.mkdir(parents=True, exist_ok=True)

        self.listener = UDPListener(port=udp_port)
        self.devices: dict[str, DeviceInfo] = {}
        self.recording: Optional[ActiveCapture] = None
        self.ws_clients: set = set()
        self._broadcast_task: Optional[asyncio.Task] = None
        self._started = False

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

        # Write to active capture if recording and shape matches
        if self.recording and self.recording.device_id == pkt.device_id:
            mat = pkt.data.get("matrix")
            if mat:
                flat = [v for col in mat for v in col]
                self.recording.writer.write_row(pkt.receive_time, flat, [])

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
        return {"type": "tick", "devices": devices_out, "recording": rec}

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

"""Shared builders for the export and shutdown tests: a real AppState (no
sockets) that records takes through the normal packet path."""

import time

from openmuscle.protocol.schema import CURRENT_VERSION, OpenMusclePacket
from openmuscle.web.state import AppState


def make_state(captures_dir, port=53990):
    return AppState(udp_port=port, captures_dir=str(captures_dir), enable_discovery=False)


def band_packet(t, i, device_id="fg-1"):
    # [cols][rows] matrix as the receiver delivers it (15 cols of 4 rows).
    swing = (i % 7) * 30
    matrix = [[100 + swing + c for _ in range(4)] for c in range(15)]
    return OpenMusclePacket(version=CURRENT_VERSION, device_type="flexgrid",
                            device_id=device_id, timestamp_ms=int(t * 1000),
                            data={"matrix": matrix, "rows": 4, "cols": 15},
                            receive_time=t)


def labeler_packet(t, i):
    v = (i % 7) * 100
    return OpenMusclePacket(version=CURRENT_VERSION, device_type="lask5",
                            device_id="lask5-1", timestamp_ms=int(t * 1000),
                            data={"values": [v, v + 1, v + 2, v + 3]},
                            receive_time=t)


def begin_take(state, name, frames=60, notes=None):
    """Start a take and feed it `frames` paired frames, but do NOT stop it."""
    t0 = time.time()
    state._handle_packet(band_packet(t0, 0))
    state._handle_packet(labeler_packet(t0, 0))
    rec = state.start_recording(sensor_device_id="fg-1", label_device_id="lask5-1",
                                filename=name)
    for i in range(frames):
        t = t0 + 0.01 * (i + 1)
        state._handle_packet(labeler_packet(t, i))
        state._handle_packet(band_packet(t + 0.001, i))
    if notes:
        state.write_capture_meta(rec.path.name, {"notes": notes})
    return rec


def record_take(state, name, frames=60, seconds=30.0, notes=None):
    rec = begin_take(state, name, frames=frames, notes=notes)
    rec.started_at -= seconds          # pretend the take ran this long
    return state.stop_recording()

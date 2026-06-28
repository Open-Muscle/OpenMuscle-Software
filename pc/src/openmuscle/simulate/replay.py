"""Replay a recorded capture back into a running `openmuscle web` server.

Reconstructs the original device streams from a capture CSV so the WHOLE live
pipeline (ingest -> recorder -> matcher -> inference -> snapshot/VR) runs with
NO hardware: FlexGrid frames go over UDP exactly as a band sends them, and Quest
hand frames go over the /ws/quest WebSocket exactly as the headset sends them.
Timing follows the capture's own timestamps, scaled by `speed`.

This is the "send past data packets to test the pipeline" path, rebuilt to
handle TWO-hand sessions (the old simulate --replay was a flat UDP line pipe
that could not reconstruct per-band frames or Quest hands).

Supports both capture layouts:
  v1: `timestamp, R{r}C{c}.., label_*`            (one band, seconds timestamp)
  v2: `ts_hub_ms, role, device_id, R{r}C{c}.., label_*, [imu_*], [forearm_*]`
A bilateral v2 capture (left + right band rows) replays BOTH bands over UDP and
BOTH Quest hands over /ws/quest, so the two-hand UX can be exercised end to end.

Pure parsing (`parse_capture`) is socket-free so tests can call it directly.
"""

import csv
import json
import socket
import time
from pathlib import Path

from openmuscle.simulate.quest_hand import JOINT_NAMES

_CHANNELS = 7          # px,py,pz,rx,ry,rz,rw per joint in the label_* block
# role -> (quest device_id, handedness) for the reconstructed labeler stream.
_SIDE = {"left": ("quest-left", "left"), "right": ("quest-right", "right")}


def _grid_dims(header):
    """(rows, cols) from the R{r}C{c} column names (rows = max r+1, cols = max c+1)."""
    rows = cols = 0
    for c in header:
        if c and c[0] == "R" and "C" in c:
            try:
                r = int(c[1:c.index("C")])
                cc = int(c[c.index("C") + 1:])
            except ValueError:
                continue
            rows = max(rows, r + 1)
            cols = max(cols, cc + 1)
    return rows, cols


def parse_capture(path):
    """Parse a v1 or v2 capture CSV into time-ordered replay frames.

    Returns (frames, info). Each frame is a dict:
        t_ms:      hub-clock ms (v1 seconds are scaled up)
        device_id: the band id (synthesized for v1)
        role:      left/right/'' (from v2; '' for v1)
        matrix:    [cols][rows] ints -- the on-wire FlexGrid shape (un-flattened
                   from the R{r}C{c} row-major columns: matrix[c][r] = flat[r*cols+c])
        joints:    list of [7]-float joint rows from the label_* block, or None
    info: {rows, is_v2, grid:[rows,cols], roles:[...], device_ids:[...], duration_ms}
    """
    path = Path(path)
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        data = [r for r in reader if r]
    idx = {name: i for i, name in enumerate(header)}
    sensor_cols = [c for c in header if c and c[0] == "R" and "C" in c]
    label_cols = [c for c in header if c.startswith("label_")]
    grid_rows, grid_cols = _grid_dims(header)
    is_v2 = "ts_hub_ms" in idx and "role" in idx and "device_id" in idx
    ts_key = "ts_hub_ms" if is_v2 else "timestamp"

    def num(row, col, default=0.0):
        try:
            return float(row[idx[col]])
        except (ValueError, KeyError, IndexError):
            return default

    frames, roles, ids = [], set(), set()
    for row in data:
        if ts_key not in idx:
            continue
        t_raw = num(row, ts_key, None if False else 0.0)
        # v1 stores a float seconds timestamp; v2 stores integer hub-ms.
        t_ms = t_raw if is_v2 else t_raw * 1000.0
        device_id = (row[idx["device_id"]] if is_v2 else "flexgrid-replay")
        role = (row[idx["role"]].strip().lower() if is_v2 else "")
        roles.add(role)
        ids.add(device_id)

        flat = [num(row, c) for c in sensor_cols]
        matrix = [[int(round(flat[r * grid_cols + c]))
                   for r in range(grid_rows)]
                  for c in range(grid_cols)] if (grid_rows and grid_cols) else []

        labels = [num(row, c) for c in label_cols]
        joints = None
        if len(labels) >= _CHANNELS:
            n = len(labels) // _CHANNELS
            joints = [labels[i * _CHANNELS:(i + 1) * _CHANNELS] for i in range(n)]

        frames.append({"t_ms": t_ms, "device_id": device_id, "role": role,
                       "matrix": matrix, "joints": joints})

    frames.sort(key=lambda fr: fr["t_ms"])
    dur = (frames[-1]["t_ms"] - frames[0]["t_ms"]) if frames else 0.0
    info = {
        "rows": len(frames),
        "is_v2": is_v2,
        "grid": [grid_rows, grid_cols],
        "roles": sorted(r for r in roles if r),
        "device_ids": sorted(ids),
        "duration_ms": dur,
        "has_quest": any(fr["joints"] for fr in frames),
    }
    return frames, info


def _flexgrid_packet(device_id, matrix, grid, t_ms):
    rows, cols = grid
    return {
        "v": "1.0", "type": "flexgrid", "id": device_id or "flexgrid-replay",
        "ts": int(t_ms) % 2**31,
        "data": {"matrix": matrix, "rows": rows, "cols": cols},
    }


def _quest_payload(joints, role, t_ms):
    qid, hand = _SIDE.get(role, ("quest-replay", "right"))
    return {
        "device_id": qid, "ts": int(t_ms) % 2**31, "handedness": hand,
        "joints": [
            {"name": JOINT_NAMES[i] if i < len(JOINT_NAMES) else f"j{i}",
             "pos": [j[0], j[1], j[2]],
             "rot": (j[3:7] if len(j) >= 7 else [0.0, 0.0, 0.0, 1.0])}
            for i, j in enumerate(joints)
        ],
        "meta": {"replay": True},
    }


def replay_capture(path, target_ip="127.0.0.1", udp_port=3141, web_port=8000,
                   speed=1.0, loop=False, stop_event=None, on_progress=None,
                   quest_sink=None, quest_ws=True):
    """Replay `path` into the live server. Blocking; run on a background thread.

    Emits one FlexGrid UDP packet per row (the band's reconstructed matrix) and,
    when the row carries Quest joints, one Quest frame for that row's side
    (left->quest-left, right->quest-right, else quest-replay). Sleeps the real
    inter-row gap divided by `speed` so match windows + framerate behave like a
    live run. `on_progress(dict)` is called with {active,frame,total,...}.

    Quest frames are delivered via `quest_sink(payload)` when given (in-process,
    same path AppState.ingest_quest_packet uses for a real headset); otherwise a
    /ws/quest WebSocket to web_port is opened (the CLI path). FlexGrid always
    goes over real UDP so the listener is exercised exactly as with hardware.
    """
    frames, info = parse_capture(path)
    if not frames:
        if on_progress:
            on_progress({"active": False, "frame": 0, "total": 0,
                         "error": "empty capture"})
        return info

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ws_conns = {}

    def send_quest(payload, qid):
        if quest_sink is not None:
            quest_sink(payload)
            return
        if not quest_ws:
            return
        if qid not in ws_conns:
            try:
                from websockets.sync.client import connect
                ws_conns[qid] = connect(f"ws://{target_ip}:{web_port}/ws/quest")
            except Exception:
                ws_conns[qid] = None
        ws = ws_conns[qid]
        if ws is not None:
            try:
                ws.send(json.dumps(payload))
            except Exception:
                ws_conns[qid] = None

    total = len(frames)
    speed = max(float(speed), 0.05)
    try:
        again = True
        while again:
            prev_t = None
            for i, fr in enumerate(frames):
                if stop_event is not None and stop_event.is_set():
                    again = False
                    break
                if prev_t is not None:
                    dt = (fr["t_ms"] - prev_t) / 1000.0 / speed
                    if dt > 0:
                        time.sleep(min(dt, 1.0))   # clamp big gaps
                prev_t = fr["t_ms"]

                pkt = _flexgrid_packet(fr["device_id"], fr["matrix"],
                                       info["grid"], fr["t_ms"])
                try:
                    sock.sendto(json.dumps(pkt).encode("utf-8"),
                                (target_ip, udp_port))
                except OSError:
                    pass

                if fr["joints"]:
                    qid = _SIDE.get(fr["role"], ("quest-replay", "right"))[0]
                    send_quest(_quest_payload(fr["joints"], fr["role"], fr["t_ms"]),
                               qid)

                if on_progress and (i % 10 == 0 or i == total - 1):
                    on_progress({"active": True, "frame": i + 1, "total": total,
                                 "capture": Path(path).name})
            if not loop:
                again = False
    finally:
        sock.close()
        for ws in ws_conns.values():
            try:
                if ws is not None:
                    ws.close()
            except Exception:
                pass
        if on_progress:
            on_progress({"active": False, "frame": total, "total": total,
                         "capture": Path(path).name})
    return info

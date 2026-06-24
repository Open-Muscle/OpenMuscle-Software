"""Parse incoming UDP datagrams into OpenMusclePacket objects.

Supports three formats:
1. New protocol: JSON with "v" field
2. Legacy FlexGrid: bare JSON array (16x4 matrix)
3. Legacy LASK5/SensorBand: Python dict with "id" field (uses ast.literal_eval)
"""

import json
import ast
import time as _time

from openmuscle.protocol.schema import OpenMusclePacket


def parse_packet(raw_bytes: bytes) -> OpenMusclePacket | None:
    """Parse a raw UDP datagram into an OpenMusclePacket.

    Returns None if the packet cannot be parsed.
    """
    text = raw_bytes.decode("utf-8", errors="replace")
    recv_ts = _time.time()

    # Try JSON first (handles new protocol + bare arrays)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Fall back to ast.literal_eval for legacy Python repr strings
        # (LASK5 sends tuples in "time" field which aren't valid JSON)
        try:
            obj = ast.literal_eval(text)
        except Exception:
            return None

    # New protocol: JSON object with "v" field
    if isinstance(obj, dict) and "v" in obj:
        # V4 discovery announce beacons share UDP 3141 with sensor frames.
        # They are NOT sensor data: route the whole announce dict through .data
        # so the listener can hand it to the DiscoveryManager and keep it out of
        # the sensor pipeline (otherwise it becomes a phantom "announce" device).
        if obj.get("type") == "announce":
            return OpenMusclePacket(
                version=obj["v"],
                device_type="announce",
                device_id=obj.get("id", ""),
                timestamp_ms=obj.get("ts", 0),
                data=obj,
                metadata={},
                receive_time=recv_ts,
            )
        return OpenMusclePacket(
            version=obj["v"],
            device_type=obj["type"],
            device_id=obj["id"],
            timestamp_ms=obj.get("ts", 0),
            data=obj.get("data", {}),
            metadata=obj.get("meta", {}),
            receive_time=recv_ts,
        )

    # Legacy FlexGrid: bare list (16x4 matrix)
    if isinstance(obj, list):
        return OpenMusclePacket(
            version="legacy",
            device_type="flexgrid",
            device_id="flexgrid-legacy",
            timestamp_ms=0,
            data={"matrix": obj, "rows": 4, "cols": 16},
            receive_time=recv_ts,
        )

    # Legacy LASK5 / SensorBand: dict with "id" but no "v"
    if isinstance(obj, dict) and "id" in obj:
        device_id = obj["id"]
        if "LASK5" in device_id:
            dtype = "lask5"
        elif device_id.startswith("OM-SB"):
            dtype = "sensorband"
        else:
            dtype = "unknown"

        data_payload = {}
        if "data" in obj:
            data_payload["values"] = obj["data"]
        if "hallIndex" in obj:
            data_payload["hall_index"] = obj["hallIndex"]

        return OpenMusclePacket(
            version="legacy",
            device_type=dtype,
            device_id=device_id,
            timestamp_ms=obj.get("ticks", 0),
            data=data_payload,
            metadata={},
            receive_time=recv_ts,
        )

    return None

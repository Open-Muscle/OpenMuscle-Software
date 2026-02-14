# om_packet.py - Standard OpenMuscle packet builder
#
# All devices use this to create uniformly structured JSON packets.
# See docs/protocol.md for the full specification.

import ujson
import time

PROTOCOL_VERSION = "1.0"

def build_packet(device_type, device_id, data, metadata=None):
    """
    Build a standard OpenMuscle JSON packet.

    Args:
        device_type: str - "flexgrid", "lask5", etc.
        device_id: str - unique device identifier
        data: dict - device-specific sensor payload
        metadata: dict or None - optional extras (battery, calibration state, etc.)

    Returns:
        bytes - JSON-encoded UTF-8 packet ready for UDP transmission
    """
    pkt = {
        "v": PROTOCOL_VERSION,
        "type": device_type,
        "id": device_id,
        "ts": time.ticks_ms(),
        "data": data,
    }
    if metadata:
        pkt["meta"] = metadata
    return ujson.dumps(pkt)

"""Standard OpenMuscle packet schema definition."""

from dataclasses import dataclass, field
from typing import Any

CURRENT_VERSION = "1.0"


@dataclass
class OpenMusclePacket:
    """Parsed representation of an OpenMuscle UDP packet.

    Attributes:
        version: Protocol version string (e.g. "1.0") or "legacy"
        device_type: Device type identifier ("flexgrid", "lask5", "sensorband", etc.)
        device_id: Unique device identifier
        timestamp_ms: Device-local timestamp in milliseconds
        data: Device-specific sensor payload dict
        metadata: Optional metadata (battery, calibration state, etc.)
        receive_time: PC-side receive timestamp (time.time())
    """

    version: str
    device_type: str
    device_id: str
    timestamp_ms: int
    data: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    receive_time: float = 0.0

    @property
    def is_legacy(self) -> bool:
        return self.version == "legacy"

    def flat_sensor_values(self) -> list:
        """Extract a flat list of sensor values from the data payload.

        Works for both flexgrid (flattens matrix) and lask5/sensorband (returns values).
        """
        if "matrix" in self.data:
            matrix = self.data["matrix"]
            return [v for col in matrix for v in col]
        if "values" in self.data:
            return list(self.data["values"])
        return []

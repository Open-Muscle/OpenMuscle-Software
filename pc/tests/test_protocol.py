"""Tests for the packet protocol parser and schema."""

import json
from openmuscle.protocol.parser import parse_packet
from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION


class TestNewProtocol:
    def test_parse_flexgrid_packet(self):
        pkt = {
            "v": "1.0",
            "type": "flexgrid",
            "id": "fg-01",
            "ts": 12345,
            "data": {"matrix": [[100, 200, 300, 400]] * 16, "rows": 4, "cols": 16},
        }
        raw = json.dumps(pkt).encode()
        result = parse_packet(raw)
        assert result is not None
        assert result.version == "1.0"
        assert result.device_type == "flexgrid"
        assert result.device_id == "fg-01"
        assert result.timestamp_ms == 12345
        assert len(result.data["matrix"]) == 16
        assert not result.is_legacy

    def test_parse_lask5_packet(self):
        pkt = {
            "v": "1.0",
            "type": "lask5",
            "id": "lask5-01",
            "ts": 99999,
            "data": {"values": [100, 200, 300, 400]},
            "meta": {"battery": 85},
        }
        raw = json.dumps(pkt).encode()
        result = parse_packet(raw)
        assert result is not None
        assert result.device_type == "lask5"
        assert result.metadata["battery"] == 85

    def test_flat_sensor_values_matrix(self):
        pkt = OpenMusclePacket(
            version="1.0", device_type="flexgrid", device_id="fg",
            timestamp_ms=0, data={"matrix": [[1, 2], [3, 4], [5, 6]]},
        )
        assert pkt.flat_sensor_values() == [1, 2, 3, 4, 5, 6]

    def test_flat_sensor_values_list(self):
        pkt = OpenMusclePacket(
            version="1.0", device_type="lask5", device_id="l5",
            timestamp_ms=0, data={"values": [10, 20, 30, 40]},
        )
        assert pkt.flat_sensor_values() == [10, 20, 30, 40]


class TestLegacyProtocol:
    def test_parse_legacy_flexgrid_bare_array(self):
        matrix = [[100, 200, 300, 400]] * 16
        raw = json.dumps(matrix).encode()
        result = parse_packet(raw)
        assert result is not None
        assert result.is_legacy
        assert result.device_type == "flexgrid"
        assert result.data["matrix"] == matrix

    def test_parse_legacy_lask5_python_repr(self):
        # Legacy LASK5 sends Python repr with tuples
        legacy = (
            "{'id': 'OM-LASK5', 'ticks': 164587, "
            "'time': (2000, 1, 1, 0, 2, 44, 5, 1), "
            "'data': [-30, -35, -30, -37]}"
        )
        raw = legacy.encode()
        result = parse_packet(raw)
        assert result is not None
        assert result.is_legacy
        assert result.device_type == "lask5"
        assert result.data["values"] == [-30, -35, -30, -37]

    def test_parse_legacy_sensorband(self):
        legacy = (
            "{'id': 'OM-SB-V1-C.0', 'ticks': 50000, "
            "'data': [100, 200, 300, 400], "
            "'hallIndex': [0, 1, 2, 3]}"
        )
        raw = legacy.encode()
        result = parse_packet(raw)
        assert result is not None
        assert result.device_type == "sensorband"
        assert result.data["hall_index"] == [0, 1, 2, 3]


class TestEdgeCases:
    def test_parse_garbage_returns_none(self):
        assert parse_packet(b"not a valid packet at all") is None

    def test_parse_empty_returns_none(self):
        assert parse_packet(b"") is None

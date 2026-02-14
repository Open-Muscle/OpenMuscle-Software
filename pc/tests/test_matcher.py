"""Tests for the temporal matcher."""

from openmuscle.protocol.schema import OpenMusclePacket
from openmuscle.receiver.matcher import TemporalMatcher


def _make_pkt(device_type, recv_time, values=None):
    return OpenMusclePacket(
        version="1.0",
        device_type=device_type,
        device_id=f"{device_type}-test",
        timestamp_ms=int(recv_time * 1000),
        data={"values": values or [0, 0, 0, 0]},
        receive_time=recv_time,
    )


class TestTemporalMatcher:
    def test_match_within_window(self):
        matcher = TemporalMatcher(window_s=0.100)
        label = _make_pkt("lask5", 1.050)
        matcher.add_label(label)

        sensor = _make_pkt("flexgrid", 1.060)
        result = matcher.match(sensor)
        assert result is not None
        assert result.device_id == "lask5-test"

    def test_no_match_outside_window(self):
        matcher = TemporalMatcher(window_s=0.100)
        label = _make_pkt("lask5", 1.000)
        matcher.add_label(label)

        sensor = _make_pkt("flexgrid", 1.200)
        result = matcher.match(sensor)
        assert result is None
        assert matcher.unpaired_count == 1

    def test_match_nearest_label(self):
        matcher = TemporalMatcher(window_s=0.100)
        matcher.add_label(_make_pkt("lask5", 1.000, [10, 20, 30, 40]))
        matcher.add_label(_make_pkt("lask5", 1.050, [50, 60, 70, 80]))
        matcher.add_label(_make_pkt("lask5", 1.090, [90, 100, 110, 120]))

        sensor = _make_pkt("flexgrid", 1.055)
        result = matcher.match(sensor)
        assert result is not None
        assert result.data["values"] == [50, 60, 70, 80]

    def test_empty_buffer_returns_none(self):
        matcher = TemporalMatcher(window_s=0.100)
        sensor = _make_pkt("flexgrid", 1.000)
        result = matcher.match(sensor)
        assert result is None

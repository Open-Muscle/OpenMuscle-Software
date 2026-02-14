"""Temporal matching of sensor packets with label packets."""

from collections import deque

from openmuscle.protocol.schema import OpenMusclePacket


class TemporalMatcher:
    """Matches sensor packets (e.g. FlexGrid) with the nearest label packet
    (e.g. LASK5) within a configurable time window.

    Usage:
        matcher = TemporalMatcher(window_s=0.100)
        matcher.add_label(label_packet)
        matched_label = matcher.match(sensor_packet)
    """

    def __init__(self, window_s: float = 0.100):
        self.window = window_s
        self._label_buffer: deque = deque()
        self.unpaired_count = 0

    def add_label(self, pkt: OpenMusclePacket):
        self._label_buffer.append(pkt)

    def match(self, sensor_pkt: OpenMusclePacket) -> OpenMusclePacket | None:
        """Find the nearest label packet within window of sensor_pkt.receive_time.

        Returns the matched label packet or None if no match within window.
        """
        ts = sensor_pkt.receive_time

        # Prune old labels
        while self._label_buffer and self._label_buffer[0].receive_time < ts - self.window:
            self._label_buffer.popleft()

        best = None
        best_gap = self.window + 1
        for label_pkt in self._label_buffer:
            gap = abs(ts - label_pkt.receive_time)
            if gap < best_gap:
                best_gap = gap
                best = label_pkt

        if best_gap > self.window:
            self.unpaired_count += 1
            return None
        return best

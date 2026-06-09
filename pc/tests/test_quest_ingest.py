"""Tests for AppState.ingest_quest_packet.

The Quest WebXR client sends hand-tracking frames over /ws/quest as JSON
payloads. ingest_quest_packet synthesizes them into an OpenMusclePacket
with device_type="quest_hand" and routes through _handle_packet, so from
the rest of the pipeline's view they're indistinguishable from UDP devices.

These tests pin:
 - the synthesized packet's device_type / device_id / data shape
 - the registered DeviceInfo state after the call
 - empty-payload drop behavior
 - graceful handling of missing pos/rot fields
"""

import pytest

from openmuscle.web.state import (
    AppState, QUEST_JOINT_CHANNEL_ORDER, _flatten_quest_joint,
)


def _fresh_state():
    """Build an AppState without spinning up the UDP listener thread."""
    s = AppState.__new__(AppState)
    s.devices = {}
    s.recording = None
    s.engine = None
    s.inference_enabled = False
    s.hand_target = None
    return s


def _full_joint(name, offset=0.0):
    """A joint dict with non-trivial pos + rot for layout assertions."""
    return {
        "name": name,
        "pos": [offset + 0.0, offset + 0.1, offset + 0.2],
        "rot": [offset + 0.3, offset + 0.4, offset + 0.5, offset + 0.6],
    }


class TestIngestQuestPacket:
    def test_full_payload_registers_device(self):
        s = _fresh_state()
        joints = [_full_joint(f"j{i}", i * 0.01) for i in range(25)]
        s.ingest_quest_packet({
            "device_id": "quest-test",
            "ts": 42,
            "handedness": "right",
            "joints": joints,
        })

        assert "quest-test" in s.devices
        d = s.devices["quest-test"]
        assert d.device_type == "quest_hand"
        # 25 joints * 7 floats per joint
        assert len(d.last_values) == 25 * 7

    def test_data_values_use_canonical_channel_order(self):
        """Each joint flattens as px,py,pz, rx,ry,rz, rw (the constant)."""
        s = _fresh_state()
        s.ingest_quest_packet({
            "joints": [{"name": "wrist",
                        "pos": [1.0, 2.0, 3.0],
                        "rot": [0.4, 0.5, 0.6, 0.7]}],
        })
        d = next(iter(s.devices.values()))
        # Layout must match QUEST_JOINT_CHANNEL_ORDER ("px","py","pz","rx",
        # "ry","rz","rw") and _flatten_quest_joint
        assert d.last_values == [1.0, 2.0, 3.0, 0.4, 0.5, 0.6, 0.7]
        # Round-trip via the helper for clarity
        assert d.last_values == _flatten_quest_joint([1, 2, 3], [0.4, 0.5, 0.6, 0.7])
        assert QUEST_JOINT_CHANNEL_ORDER == ("px", "py", "pz", "rx", "ry", "rz", "rw")

    def test_empty_joints_drops_silently(self):
        """Headset reporting tracking-lost shouldn't create a zero device."""
        s = _fresh_state()
        s.ingest_quest_packet({"device_id": "q", "joints": []})
        assert s.devices == {}

        s.ingest_quest_packet({"device_id": "q"})  # missing 'joints' key
        assert s.devices == {}

    def test_missing_pos_rot_defaults_to_identity(self):
        """A joint with only a name still produces 7 floats (zeros + identity quat)."""
        s = _fresh_state()
        s.ingest_quest_packet({"joints": [{"name": "wrist"}]})
        d = next(iter(s.devices.values()))
        assert d.last_values == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

    def test_default_device_id_when_missing(self):
        s = _fresh_state()
        s.ingest_quest_packet({"joints": [_full_joint("wrist")]})
        # Default is "quest-01" per ingest_quest_packet's signature
        assert "quest-01" in s.devices

    def test_repeated_packets_increment_packet_count(self):
        s = _fresh_state()
        for i in range(5):
            s.ingest_quest_packet({"joints": [_full_joint("wrist", offset=i * 0.01)]})
        d = next(iter(s.devices.values()))
        assert d.packets_total == 5


class TestSnapshotExposesQuestHand:
    """The desktop Studio 3D hand viewer reads each device's flat `values`
    from the /ws/live snapshot. Lock that contract: a quest_hand device must
    surface in _snapshot() as device_type 'quest_hand' with its full flat
    joint vector, so a future snapshot refactor can't silently break the
    viewer's only data source.
    """

    def test_snapshot_has_quest_device_with_flat_values(self):
        # _snapshot() touches the full inference machinery, so use a real
        # AppState (its __init__ sets engine_status etc.) rather than the bare
        # __new__ helper. The UDP listener is never started, so no socket binds.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            s = AppState(udp_port=53997, captures_dir=d)
            joints = [_full_joint(f"j{i}", i * 0.01) for i in range(25)]
            s.ingest_quest_packet({"device_id": "quest-right", "handedness": "right",
                                   "joints": joints})
            snap = s._snapshot()
            quest = [dev for dev in snap["devices"] if dev["device_type"] == "quest_hand"]
            assert len(quest) == 1
            # 25 joints * 7 floats -> the viewer slices [i*7 .. i*7+6] per joint.
            assert len(quest[0]["values"]) == 25 * 7
            assert quest[0]["device_id"] == "quest-right"

"""Gamepad label ingest (Tory: plug in a USB controller, capture its inputs).

A browser Gamepad-API frame pushed over /ws/gamepad must synthesize a
device_type="gamepad" label device that flows through the same packet path as
the Quest and LASK5, so the recorder/matcher/snapshot treat it identically."""

import pytest

from openmuscle.web.state import AppState


def make_state(tmp_path):
    return AppState(udp_port=53997, captures_dir=str(tmp_path), enable_discovery=False)


def test_ingest_registers_gamepad_device(tmp_path):
    s = make_state(tmp_path)
    s.ingest_gamepad_packet({
        "device_id": "gamepad-0", "ts": 123, "id": "Xbox Controller",
        "axes": [-0.5, 0.0, 1.0, -1.0],
        "buttons": [0.0, 1.0, 0.25, 0.0, 0.0, 0.0],
    })
    dev = s.devices.get("gamepad-0")
    assert dev is not None
    assert dev.device_type == "gamepad"
    # values = axes (4) followed by buttons (6) = 10 label channels, in order.
    assert list(dev.last_values) == [-0.5, 0.0, 1.0, -1.0, 0.0, 1.0, 0.25, 0.0, 0.0, 0.0]


def test_gamepad_appears_in_snapshot_as_label_device(tmp_path):
    s = make_state(tmp_path)
    s.ingest_gamepad_packet({"device_id": "gamepad-0", "axes": [0.1, 0.2],
                             "buttons": [1.0]})
    snap = s._snapshot()
    dev = next((d for d in snap["devices"] if d["device_id"] == "gamepad-0"), None)
    assert dev is not None and dev["device_type"] == "gamepad"


def test_empty_frame_is_dropped(tmp_path):
    s = make_state(tmp_path)
    s.ingest_gamepad_packet({"device_id": "gamepad-0", "axes": [], "buttons": []})
    assert "gamepad-0" not in s.devices


def test_gamepad_is_auto_pickable_lowest_priority(tmp_path):
    # A lone controller is a valid auto-pick label; a quest present still wins.
    assert "gamepad" in AppState.AUTO_LABEL_TYPE_PREFERENCE
    assert (AppState.AUTO_LABEL_TYPE_PREFERENCE.index("gamepad")
            > AppState.AUTO_LABEL_TYPE_PREFERENCE.index("quest_hand"))
    s = make_state(tmp_path)
    s.ingest_gamepad_packet({"device_id": "gamepad-0", "axes": [0.0], "buttons": [0.0]})
    assert s._auto_pick_label() == "gamepad-0"


def test_gamepad_default_window_present(tmp_path):
    assert AppState.DEFAULT_WINDOW_MS_BY_TYPE.get("gamepad") == 120

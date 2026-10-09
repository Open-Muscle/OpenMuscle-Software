"""Session participant code: one field, stored under both `wearer` and `subject`,
seeded into capture meta; session Edit saves through PUT; "both arms" is stored.

Same AppState-without-listener harness as test_quality_persist.py.
"""

import asyncio
import time

from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION
from openmuscle.web.app import create_app
from openmuscle.web.state import AppState


def _state(tmp_path, port=53995):
    return AppState(udp_port=port, captures_dir=str(tmp_path), enable_discovery=False)


def _band(did, t, val=100):
    matrix = [[val + c for _ in range(4)] for c in range(15)]
    return OpenMusclePacket(version=CURRENT_VERSION, device_type="flexgrid",
                            device_id=did, timestamp_ms=int(t * 1000),
                            data={"matrix": matrix, "rows": 4, "cols": 15},
                            receive_time=t)


def _route(app, path, method):
    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", ()):
            return r.endpoint
    raise AssertionError("no route {} {}".format(method, path))


# ----- one code under both keys -----

def test_wearer_alone_fills_both_keys(tmp_path):
    s = _state(tmp_path)
    sess = s.start_session(name="a", wearer="P01")
    assert sess["wearer"] == "P01" and sess["subject"] == "P01"


def test_subject_alone_fills_both_keys(tmp_path):
    s = _state(tmp_path)
    sess = s.start_session(name="a", subject="P02")
    assert sess["wearer"] == "P02" and sess["subject"] == "P02"


def test_code_is_trimmed_and_wearer_wins(tmp_path):
    s = _state(tmp_path)
    sess = s.start_session(name="a", wearer="  P03 ", subject="other")
    assert sess["wearer"] == "P03" and sess["subject"] == "P03"


def test_arm_both_is_stored(tmp_path):
    s = _state(tmp_path)
    sess = s.start_session(name="a", arm="both")
    assert sess["arm"] == "both"
    assert s.get_session(sess["id"])["arm"] == "both"


def test_unknown_arm_is_dropped(tmp_path):
    s = _state(tmp_path)
    assert s.start_session(name="a", arm="upside-down")["arm"] is None


# ----- capture meta and the captures list -----

def test_capture_meta_and_list_show_the_code(tmp_path):
    s = _state(tmp_path)
    s.start_session(name="a", wearer="P01", arm="both")
    s._handle_packet(_band("fg-1", time.time()))
    s.start_recording(sensor_device_id="fg-1", label_device_id="", filename="t1")
    s.stop_recording()
    meta = s.read_capture_meta("t1.csv")
    assert meta["subject"] == "P01"
    assert meta["arm"] == "both"
    assert meta["auto"]["session_wearer"] == "P01"
    row = [c for c in s.list_captures() if c["name"] == "t1.csv"][0]
    assert row["meta"]["subject"] == "P01"
    assert row["meta"]["arm"] == "both"


def test_old_take_falls_back_to_session_wearer(tmp_path):
    """A take recorded before the code was seeded into meta.subject still shows it."""
    s = _state(tmp_path)
    (tmp_path / "old.csv").write_text("a,b\n1,2\n")
    s.write_capture_meta("old.csv", {"auto": {"session_wearer": "P09"}})
    row = [c for c in s.list_captures() if c["name"] == "old.csv"][0]
    assert row["meta"]["subject"] == "P09"


# ----- edit -----

def test_update_keeps_both_keys_in_step(tmp_path):
    s = _state(tmp_path)
    sess = s.start_session(name="a", wearer="P01")
    out = s.update_session(sess["id"], {"subject": "P07"})
    assert out["wearer"] == "P07" and out["subject"] == "P07"
    out = s.update_session(sess["id"], {"wearer": "P08", "subject": "P08"})
    assert out["wearer"] == "P08" and out["subject"] == "P08"
    out = s.update_session(sess["id"], {"wearer": ""})
    assert out["wearer"] == "" and out["subject"] == ""


def test_update_arm_both_and_clear(tmp_path):
    s = _state(tmp_path)
    sess = s.start_session(name="a", arm="left")
    assert s.update_session(sess["id"], {"arm": "both"})["arm"] == "both"
    assert s.update_session(sess["id"], {"arm": "sideways"})["arm"] == "both"
    assert s.update_session(sess["id"], {"arm": ""})["arm"] is None


def test_update_protects_fields_and_keeps_name_when_blank(tmp_path):
    s = _state(tmp_path)
    sess = s.start_session(name="morning", wearer="P01")
    out = s.update_session(sess["id"], {
        "id": "hacked", "started_at": 1, "captures": ["x.csv"], "capture_count": 9,
        "ended_at": 5, "name": "   "})
    assert out["id"] == sess["id"]
    assert out["started_at"] == sess["started_at"]
    assert out["captures"] == [] and out["capture_count"] == 0
    assert out["ended_at"] is None
    assert out["name"] == "morning"
    assert out["modified_at"] >= sess["started_at"]


def test_update_renames_and_syncs_the_active_session(tmp_path):
    s = _state(tmp_path)
    sess = s.start_session(name="old name", wearer="P01")
    out = s.update_session(sess["id"], {"name": "new name", "wearer": "P02"})
    assert out["name"] == "new name"
    assert s.active_session["name"] == "new name"
    assert s.active_session["wearer"] == "P02"
    # Captures made after the edit carry the new code.
    s._handle_packet(_band("fg-1", time.time()))
    s.start_recording(sensor_device_id="fg-1", label_device_id="", filename="t2")
    s.stop_recording()
    assert s.read_capture_meta("t2.csv")["subject"] == "P02"


def test_update_unknown_session_is_none(tmp_path):
    assert _state(tmp_path).update_session("nope", {"name": "x"}) is None


def test_edit_over_http(tmp_path):
    app = create_app(udp_port=53994, captures_dir=str(tmp_path))
    start = _route(app, "/api/sessions", "POST")
    put = _route(app, "/api/sessions/{session_id}", "PUT")
    get = _route(app, "/api/sessions/{session_id}", "GET")
    body = start.__annotations__["body"]
    made = asyncio.run(start(body(name="first", wearer="P01", arm="both")))
    assert made["arm"] == "both"
    out = asyncio.run(put(made["id"], {"name": "second", "wearer": "P05",
                                       "subject": "P05", "arm": "right",
                                       "notes": "hello"}))
    assert out["name"] == "second" and out["subject"] == "P05"
    again = asyncio.run(get(made["id"]))
    assert again["arm"] == "right" and again["notes"] == "hello"
    assert again["id"] == made["id"] and again["started_at"] == made["started_at"]


# ----- the cross-wearer guard still sees the code -----

def test_guard_compares_the_unified_code(tmp_path):
    s = _state(tmp_path)

    class _Eng:
        metadata = {"wearer": "p01"}
    s.engines["left"] = _Eng()
    assert s.start_session(name="a", subject="P02")["model_wearer_mismatch"] == ["left"]
    s.end_session()
    assert s.start_session(name="b", subject="P01")["model_wearer_mismatch"] == []

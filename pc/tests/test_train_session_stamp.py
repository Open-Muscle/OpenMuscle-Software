"""Training provenance + the PC train trap.

Two guarantees from train_from_captures:

1. Session provenance: a training run during an active session stamps
   session_id / session_name / wearer plus the source capture names into the
   fresh model dir's metadata.json. registry.list_models reads metadata.json
   wholesale, so /api/models carries the fields for free and the Models panel
   can scope its list to the active session.

2. The bilateral-pivot warning: a role-less train on a two-hand capture
   pivots to the Left||Right concat (120 features on real V4 bands), a model
   the per-band router can never run (separate-model-per-hand needs one
   single-arm model per hand). It stays allowed for CLI/offline analysis but
   the result payload and log buffer must say so out loud; the silent version
   of this cost two capture sessions of garbage ghosts.

Uses tiny synthetic schema-v2 CSVs (6 features, 2 labels, 4-tree forests) so
the whole file runs in a few seconds.
"""

import pytest
import csv
import json
from pathlib import Path

from openmuscle.web.state import AppState

pytestmark = pytest.mark.usefixtures("tiny_training_ok")


def _write_v2_csv(path, n=40, roles=("left", "right")):
    """Tiny schema-v2 long capture. Both-role files interleave left/right
    rows sharing one ts_hub_ms, so the bilateral pivot's 50ms merge window
    always pairs them. Labels vary with the row index (a constant label makes
    r2 undefined)."""
    feat_cols = [f"R0C{c}" for c in range(6)]
    header = ["ts_hub_ms", "role", "device_id"] + feat_cols + ["label_0", "label_1"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        ts = 1_000_000
        for i in range(n):
            for role in roles:
                base = 2.0 if role == "left" else 100.0
                feats = [base + ((i + c) % 7) for c in range(6)]
                labels = [base / 100.0 + (i % 5) * 0.01,
                          base / 50.0 + (i % 3) * 0.01]
                w.writerow([ts, role, f"fg-{role}"] + feats + labels)
            ts += 40


def _state(tmp_path, port):
    caps = tmp_path / "captures"
    caps.mkdir(exist_ok=True)
    return AppState(udp_port=port, captures_dir=str(caps),
                    enable_discovery=False)


def test_train_stamps_session_provenance(tmp_path, monkeypatch):
    # ModelRegistry writes under the data root; keep it in the sandbox.
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    s = _state(tmp_path, 53880)
    _write_v2_csv(s.captures_dir / "take1.csv")
    s.active_session = {"id": "s_stamp01", "name": "stamp check",
                        "wearer": "wearer-a"}

    result = s.train_from_captures(["take1.csv"], n_estimators=4,
                                   activate=True, role="left")

    meta_path = Path(result["model_path"]).parent / "metadata.json"
    meta = json.loads(meta_path.read_text())
    assert meta["session_id"] == "s_stamp01"
    assert meta["session_name"] == "stamp check"
    assert meta["wearer"] == "wearer-a"
    assert meta["captures"] == ["take1.csv"]
    assert meta["metrics"]["role"] == "left"

    # /api/models carries the stamp for free and flags the per-hand slot the
    # activate=True load just filled.
    listed = {m["path"]: m for m in s.list_models()}
    entry = listed[result["model_path"]]
    assert entry["session_id"] == "s_stamp01"
    assert entry["active_roles"] == ["left"]
    assert entry["active"] is False          # shared slot untouched

    # The WS snapshot exposes the occupied slots for the Models panel badges.
    snap = s._inference_snapshot()
    assert snap["active_models"]["left"] == Path(result["model_path"]).parent.name
    assert snap["active_models"]["right"] is None
    assert snap["active_models"]["shared"] is None

    # A per-hand train is not a bilateral pivot: no warning.
    assert result["warning"] is None
    assert result["activate_error"] is None


def test_roleless_bilateral_train_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    s = _state(tmp_path, 53881)
    _write_v2_csv(s.captures_dir / "two_hand.csv")

    result = s.train_from_captures(["two_hand.csv"], n_estimators=4,
                                   activate=False)

    # Role-less two-hand train pivots Left||Right: 6 + 6 = 12 features here
    # (120 on real V4 bands). Payload + log must carry the warning; the run
    # itself stays allowed for CLI/offline use.
    assert result["metrics"]["n_features"] == 12
    assert result["warning"] is not None
    assert "bilateral" in result["warning"]
    assert "role=left/right" in result["warning"]
    assert any(e["level"] == "WARN" and "bilateral" in e["message"]
               for e in s.log_buffer.entries())

    # No active session: source captures still recorded, but no session
    # fields leak into the model's metadata.
    meta_path = Path(result["model_path"]).parent / "metadata.json"
    meta = json.loads(meta_path.read_text())
    assert meta["captures"] == ["two_hand.csv"]
    assert "session_id" not in meta


def test_single_role_roleless_train_has_no_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    s = _state(tmp_path, 53882)
    _write_v2_csv(s.captures_dir / "one_hand.csv", roles=("left",))

    result = s.train_from_captures(["one_hand.csv"], n_estimators=4,
                                   activate=False)

    # Single-band capture through the legacy pooled path: no pivot, no
    # warning, plain 6-feature single-source model.
    assert result["warning"] is None
    assert result["metrics"]["n_features"] == 6


def test_start_session_warns_on_wearer_mismatch(tmp_path, monkeypatch):
    """The stale-model trap: a restart auto-restored the previous wearer's models
    as newest-on-disk right before a new wearer's session (cross-wearer
    inference measured ~47x worse). start_session must compare each loaded
    per-hand engine's stamped wearer against the session's and warn."""
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    s = _state(tmp_path, 53883)
    _write_v2_csv(s.captures_dir / "take1.csv")

    # Train + activate a left-hand model stamped with a DIFFERENT wearer.
    s.active_session = {"id": "s_other01", "name": "other take",
                        "wearer": "wearer-b"}
    s.train_from_captures(["take1.csv"], n_estimators=4,
                          activate=True, role="left")
    s.active_session = None

    session = s.start_session(name="a take", wearer="wearer-a")
    assert session["model_wearer_mismatch"] == ["left"]
    assert any(e["level"] == "WARN"
               and "left-hand model was trained on participant 'wearer-b'" in e["message"]
               for e in s.log_buffer.entries())
    s.end_session()

    # Matching wearer (case/whitespace-insensitive): no mismatch, no warning.
    session2 = s.start_session(name="b again", wearer="  Wearer-B ")
    assert session2["model_wearer_mismatch"] == []
    s.end_session()

    # Unstamped model (no wearer in metadata): defensive no-op.
    s.engines["left"].metadata.pop("wearer", None)
    s.engines["left"].metadata.pop("participant", None)
    session3 = s.start_session(name="anon model", wearer="wearer-a")
    assert session3["model_wearer_mismatch"] == []


# ----------------------------------------------------------------------------
# Participant and band-unit stamp + the guard that blocks
# ----------------------------------------------------------------------------

def _flex_dev(s, did, role):
    from openmuscle.web.state import DeviceInfo
    d = DeviceInfo(device_id=did, device_type="flexgrid")
    d.rows, d.cols = 4, 15
    s.devices[did] = d
    s._role_by_device[did] = role


def _train_left(s, session_wearer=None, name="take1.csv"):
    s.active_session = ({"id": "s_g", "name": "g", "wearer": session_wearer}
                        if session_wearer is not None else None)
    res = s.train_from_captures([name], n_estimators=4, activate=True, role="left")
    s.active_session = None
    return res


def test_train_stamps_participant_and_bands_without_a_session(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    s = _state(tmp_path, 53860)
    _write_v2_csv(s.captures_dir / "take1.csv")
    # no active session: the recording's own meta says who and what
    s.write_capture_meta("take1.csv", {
        "subject": "  P-07 ", "label_source": "webcam",
        "roles": {"fg-left": "left", "fg-right": "right"},
        "auto": {"session_wearer": "P-07"}})
    res = _train_left(s)
    meta = json.loads((Path(res["model_path"]).parent / "metadata.json").read_text())
    assert meta["participant"] == "p-07"                      # normalised
    assert meta["band_device_ids"] == {"left": ["fg-left"]}   # from the CSV rows
    assert meta["label_source"] == "webcam"
    assert "session_id" not in meta
    assert res["provenance"]["participant"] == "p-07"
    # the engine carries it, and /api/models lists it
    assert s.engines["left"].metadata["participant"] == "p-07"
    assert s.list_models()[0]["participant"] == "p-07"


def test_participant_match_is_case_and_space_insensitive(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    s = _state(tmp_path, 53861)
    _write_v2_csv(s.captures_dir / "take1.csv")
    _train_left(s, session_wearer="P-07")
    s.active_session = {"id": "s2", "name": "later", "wearer": "  p-07 "}
    assert [i for i in s.model_guard_issues() if i["kind"] == "participant"] == []
    s.active_session = {"id": "s3", "name": "other", "wearer": "P-08"}
    issues = [i for i in s.model_guard_issues() if i["kind"] == "participant"]
    assert len(issues) == 1 and issues[0]["blocking"] is True
    assert issues[0]["slot"] == "left"
    assert "p-07" in issues[0]["message"] and "p-08" in issues[0]["message"]


def test_swapping_the_band_on_an_arm_blocks_prediction_until_confirmed(tmp_path, monkeypatch):
    from openmuscle.web.state import ModelGuardError
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    s = _state(tmp_path, 53862)
    _write_v2_csv(s.captures_dir / "take1.csv")
    _train_left(s, session_wearer="P-07")                   # trained with band "fg-left"

    _flex_dev(s, "fg-left", "left")                         # the same unit on the left arm
    assert s.model_guard_issues() == []
    s.set_inference_enabled(True)
    assert s.inference_enabled is True
    s.set_inference_enabled(False)

    s.devices.clear()
    _flex_dev(s, "fg-OTHER", "left")                        # a different unit now tagged left
    issues = s.model_guard_issues()
    assert [i["kind"] for i in issues] == ["band_unit"]
    assert "fg-left" in issues[0]["message"] and "fg-OTHER" in issues[0]["message"]
    assert "left" in issues[0]["message"]
    with pytest.raises(ModelGuardError) as ei:
        s.set_inference_enabled(True)                       # blocked
    assert ei.value.issues and s.inference_enabled is False
    # the snapshot carries the same issues for the amber banner
    assert s._inference_snapshot()["guard"][0]["kind"] == "band_unit"
    s.set_inference_enabled(True, force=True)               # explicit click
    assert s.inference_enabled is True


def test_models_with_no_stamp_are_unknown_not_blocked(tmp_path, monkeypatch):
    from tests.test_model_persistence import make_model_dir
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    path = make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 6)
    s = _state(tmp_path, 53863)
    s.set_model_role("left", str(path))
    s.active_session = {"id": "s", "name": "n", "wearer": "P-01"}
    _flex_dev(s, "fg-1", "left")
    assert s.model_guard_issues() == []
    s.set_inference_enabled(True)
    assert s.list_models()[0]["participant"] == ""          # the table says 'unknown participant'


def test_start_session_reports_the_guard_in_words(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    s = _state(tmp_path, 53864)
    _write_v2_csv(s.captures_dir / "take1.csv")
    _train_left(s, session_wearer="P-07")
    session = s.start_session(name="next", wearer="P-09")
    assert session["model_wearer_mismatch"] == ["left"]
    assert any(i["kind"] == "participant" and "p-09" in i["message"]
               for i in session["model_guard"])


def test_http_enabled_returns_409_with_issues_then_accepts_force(tmp_path, monkeypatch):
    import asyncio
    from openmuscle.web.app import create_app
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    caps = tmp_path / "caps"
    caps.mkdir()
    app = create_app(udp_port=53865, captures_dir=str(caps))
    s = app.state.app_state
    _write_v2_csv(caps / "take1.csv")
    _train_left(s, session_wearer="P-07")
    s.active_session = {"id": "x", "name": "x", "wearer": "P-99"}
    route = next(r.endpoint for r in app.routes
                 if getattr(r, "path", None) == "/api/inference/enabled")
    body_cls = route.__annotations__["body"]
    resp = asyncio.run(route(body_cls(enabled=True)))
    assert resp.status_code == 409
    payload = json.loads(resp.body)
    assert payload["can_override"] is True and payload["issues"]
    assert "p-07" in payload["detail"]
    ok = asyncio.run(route(body_cls(enabled=True, force=True)))
    assert ok["enabled"] is True

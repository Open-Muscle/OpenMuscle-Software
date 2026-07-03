"""Training provenance + the PC train trap (board #0314-adjacent).

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
   of this cost Tory two capture sessions of garbage ghosts.

Uses tiny synthetic schema-v2 CSVs (6 features, 2 labels, 4-tree forests) so
the whole file runs in a few seconds.
"""

import csv
import json
from pathlib import Path

from openmuscle.web.state import AppState


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
    # ModelRegistry writes to CWD-relative data/models; keep it in the sandbox.
    monkeypatch.chdir(tmp_path)
    s = _state(tmp_path, 53880)
    _write_v2_csv(s.captures_dir / "take1.csv")
    s.active_session = {"id": "s_stamp01", "name": "stamp check",
                        "wearer": "tory"}

    result = s.train_from_captures(["take1.csv"], n_estimators=4,
                                   activate=True, role="left")

    meta_path = Path(result["model_path"]).parent / "metadata.json"
    meta = json.loads(meta_path.read_text())
    assert meta["session_id"] == "s_stamp01"
    assert meta["session_name"] == "stamp check"
    assert meta["wearer"] == "tory"
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
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
    s = _state(tmp_path, 53882)
    _write_v2_csv(s.captures_dir / "one_hand.csv", roles=("left",))

    result = s.train_from_captures(["one_hand.csv"], n_estimators=4,
                                   activate=False)

    # Single-band capture through the legacy pooled path: no pivot, no
    # warning, plain 6-feature single-source model.
    assert result["warning"] is None
    assert result["metrics"]["n_features"] == 6


def test_start_session_warns_on_wearer_mismatch(tmp_path, monkeypatch):
    """The CLARK trap: a restart auto-restored the previous wearer's models
    as newest-on-disk right before a new wearer's session (cross-wearer
    inference measured ~47x worse). start_session must compare each loaded
    per-hand engine's stamped wearer against the session's and warn."""
    monkeypatch.chdir(tmp_path)
    s = _state(tmp_path, 53883)
    _write_v2_csv(s.captures_dir / "take1.csv")

    # Train + activate a left-hand model stamped with a DIFFERENT wearer.
    s.active_session = {"id": "s_clark01", "name": "clark take",
                        "wearer": "clark"}
    s.train_from_captures(["take1.csv"], n_estimators=4,
                          activate=True, role="left")
    s.active_session = None

    session = s.start_session(name="tory take", wearer="tory")
    assert session["model_wearer_mismatch"] == ["left"]
    assert any(e["level"] == "WARN"
               and "left-hand model was trained on wearer 'clark'" in e["message"]
               for e in s.log_buffer.entries())
    s.end_session()

    # Matching wearer (case/whitespace-insensitive): no mismatch, no warning.
    session2 = s.start_session(name="clark again", wearer="  Clark ")
    assert session2["model_wearer_mismatch"] == []
    s.end_session()

    # Unstamped model (no wearer in metadata): defensive no-op.
    s.engines["left"].metadata.pop("wearer", None)
    session3 = s.start_session(name="anon model", wearer="tory")
    assert session3["model_wearer_mismatch"] == []

"""Restart-safety tests: per-hand models auto-restore
from the registry on server start, and the load-time feature-count guard refuses
models trained on a different sensor layout (the stale-64-feature trap)."""

import json
import pickle
import time

import pytest
from sklearn.dummy import DummyRegressor

from openmuscle.web.inference import InferenceEngine
from openmuscle.web.state import AppState


def make_model_dir(base, name, role, n_features, n_labels=4, order=0, extra=None):
    """Fabricate a registry entry: <data root>/models/<name>/model.pkl + metadata.json,
    the exact layout ModelRegistry.save produces (role inside metrics)."""
    d = base / "models" / name
    d.mkdir(parents=True)
    model = DummyRegressor(strategy="constant", constant=[0.5] * n_labels)
    X = [[0.0] * n_features, [1.0] * n_features]
    model.fit(X, [[0.5] * n_labels, [0.5] * n_labels])
    with open(d / "model.pkl", "wb") as f:
        pickle.dump(model, f)
    with open(d / "metadata.json", "w") as f:
        json.dump({"name": name, "created": name.split("_", 1)[-1],
                   "metrics": {"role": role, "n_features": n_features,
                               "n_labels": n_labels, "r2": 0.9},
                   **(extra or {})}, f)
    # Keep directory mtimes distinct/ordered like real timestamped runs.
    t = time.time() - 1000 + order
    import os
    os.utime(d, (t, t))
    return d / "model.pkl"


def make_state(tmp_path):
    return AppState(udp_port=53998, captures_dir=str(tmp_path / "caps"))


class TestAutoRestore:
    def test_autoloads_newest_per_role(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
        make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 60, order=0)
        newer_left = make_model_dir(tmp_path, "random_forest_20260102_000000", "left", 60, order=1)
        right = make_model_dir(tmp_path, "random_forest_20260102_000001", "right", 60, order=2)
        s = make_state(tmp_path)
        assert s.engines["left"] is not None and s.engines["right"] is not None
        # Newest left wins (registry sorts by dir name = timestamp). The registry
        # hands back cwd-relative paths; compare resolved.
        from pathlib import Path
        assert Path(s.engines["left"].model_path).resolve() == newer_left.resolve()
        assert Path(s.engines["right"].model_path).resolve() == right.resolve()
        # Restart-safety: models come back, but PAUSED, with a banner that says so.
        assert s.inference_enabled is False
        assert s.restored_banner and "press Predict" in s.restored_banner

    def test_untagged_models_never_autoload(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
        # A stale pre-role model (no role in metrics) -- e.g. the 64-feature one.
        make_model_dir(tmp_path, "random_forest_20260101_000000", None, 64)
        s = make_state(tmp_path)
        assert s.engines["left"] is None and s.engines["right"] is None
        assert s.inference_enabled is False

    def test_no_models_dir_is_fine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
        s = make_state(tmp_path)
        assert s.engines["left"] is None and s.engines["right"] is None


class TestFeatureGuard:
    def test_engine_refuses_mismatched_features(self, tmp_path):
        path = make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 64)
        with pytest.raises(ValueError) as ei:
            InferenceEngine(str(path), expected_features=60)
        msg = str(ei.value)
        assert "64" in msg and "60" in msg and "different sensor layout" in msg

    def test_engine_loads_when_matching_or_unknown(self, tmp_path):
        path = make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 60)
        assert InferenceEngine(str(path), expected_features=60).n_features == 60
        assert InferenceEngine(str(path)).n_features == 60   # no band seen yet

    def test_set_model_role_guards_against_live_band(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
        stale = make_model_dir(tmp_path, "random_forest_20260101_000000", None, 64)
        s = make_state(tmp_path)
        # Simulate a live 15x4 V4 band tagged left.
        from openmuscle.web.state import DeviceInfo
        d = DeviceInfo(device_id="fg-1", device_type="flexgrid")
        d.rows, d.cols = 4, 15
        s.devices["fg-1"] = d
        s._role_by_device = {"fg-1": "left"}
        with pytest.raises(ValueError):
            s.set_model_role("left", str(stale))
        assert s.engines["left"] is None


# ----------------------------------------------------------------------------
# Restore and routing safety (active.json, newest-activation-wins routing)
# ----------------------------------------------------------------------------

def _flex(s, did, role, rows=4, cols=15):
    from openmuscle.web.state import DeviceInfo
    d = DeviceInfo(device_id=did, device_type="flexgrid")
    d.rows, d.cols = rows, cols
    s.devices[did] = d
    s._role_by_device[did] = role


class TestActiveSlots:
    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))

    def test_shared_model_survives_restart(self, tmp_path):
        shared = make_model_dir(tmp_path, "random_forest_20260101_000000", None, 60)
        s1 = make_state(tmp_path)
        s1.set_model(str(shared))
        assert (tmp_path / "models" / "active.json").exists()
        s2 = make_state(tmp_path)               # a restart
        assert s2.engine is not None
        assert s2.engine.name == "random_forest_20260101_000000"
        assert s2.engines["left"] is None and s2.engines["right"] is None

    def test_restored_models_start_paused_with_a_banner(self, tmp_path):
        shared = make_model_dir(tmp_path, "random_forest_20260101_000000", None, 60,
                                extra={"participant": "p-07"})
        s1 = make_state(tmp_path)
        s1.set_model(str(shared))
        s2 = make_state(tmp_path)
        assert s2.inference_enabled is False
        assert "Restored the model" in s2.restored_banner
        assert "p-07" in s2.restored_banner and "press Predict" in s2.restored_banner
        assert s2._inference_snapshot()["restored_banner"] == s2.restored_banner
        s2.set_inference_enabled(True)
        assert s2.restored_banner is None          # acting on it clears the note

    def test_restore_follows_saved_selection_not_newest_on_disk(self, tmp_path):
        old_left = make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 60, order=0)
        make_model_dir(tmp_path, "random_forest_20260301_000000", "left", 60, order=5)
        s1 = make_state(tmp_path)
        s1.set_model_role("left", str(old_left))     # the operator's explicit choice
        s2 = make_state(tmp_path)
        assert s2.engines["left"].name == "random_forest_20260101_000000"

    def test_restore_with_missing_file_is_skipped_cleanly(self, tmp_path):
        left = make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 60)
        s1 = make_state(tmp_path)
        s1.set_model_role("left", str(left))
        left.unlink()
        s2 = make_state(tmp_path)
        assert s2.engines["left"] is None
        assert s2.restored_banner is None

    def test_cli_model_flag_still_runs_immediately(self, tmp_path):
        shared = make_model_dir(tmp_path, "random_forest_20260101_000000", None, 60)
        s = AppState(udp_port=53998, captures_dir=str(tmp_path / "caps"),
                     model_path=str(shared))
        assert s.inference_enabled is True and s.restored_banner is None


class TestRoutingPrecedence:
    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))

    def test_shared_model_trained_after_a_two_band_day_drives_the_band(self, tmp_path):
        left = make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 60)
        right = make_model_dir(tmp_path, "random_forest_20260101_000001", "right", 60)
        shared = make_model_dir(tmp_path, "random_forest_20260102_000000", None, 60)
        s = make_state(tmp_path)
        s.set_model_role("left", str(left))
        s.set_model_role("right", str(right))
        assert s._engine_for_role("left") is s.engines["left"]
        s.set_model(str(shared))                    # today: one band, one pooled model
        assert s._engine_for_role("left") is s.engine
        assert s._engine_for_role("right") is s.engine
        assert s._engine_for_role("") is s.engine
        # loading the left model again afterwards puts it back in charge of the left band
        s.set_model_role("left", str(right))
        s.set_model_role("left", str(left))
        assert s._engine_for_role("left") is s.engines["left"]
        assert s._engine_for_role("right") is s.engine

    def test_routing_after_restart_keeps_the_same_winner(self, tmp_path):
        left = make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 60)
        shared = make_model_dir(tmp_path, "random_forest_20260102_000000", None, 60)
        s1 = make_state(tmp_path)
        s1.set_model_role("left", str(left))
        s1.set_model(str(shared))
        s2 = make_state(tmp_path)
        assert s2._engine_for_role("left") is s2.engine

    def test_snapshot_says_which_model_drives_which_band(self, tmp_path):
        left = make_model_dir(tmp_path, "random_forest_20260101_000000", "left", 60)
        shared = make_model_dir(tmp_path, "random_forest_20260102_000000", None, 60)
        s = make_state(tmp_path)
        s.set_model_role("left", str(left))
        s.set_model(str(shared))
        _flex(s, "fg-1", "left")
        _flex(s, "fg-2", "right")
        snap = s._inference_snapshot()
        by_dev = {b["device_id"]: b for b in snap["routing"]}
        assert by_dev["fg-1"]["model"] == "random_forest_20260102_000000"
        assert by_dev["fg-1"]["slot"] == "shared"
        assert "Left band: random_forest_20260102_000000" in snap["routing_text"]
        assert "Right band: random_forest_20260102_000000" in snap["routing_text"]


# ----------------------------------------------------------------------------
# Unique model directories and metadata beside a CLI pickle
# ----------------------------------------------------------------------------

class TestUniqueModelDirs:
    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))

    def test_left_and_right_saved_back_to_back_do_not_collide(self, tmp_path):
        from pathlib import Path
        from openmuscle.ml.registry import ModelRegistry
        reg = ModelRegistry(str(tmp_path / "reg"))
        model = DummyRegressor(strategy="constant", constant=[0.5, 0.5]).fit(
            [[0.0], [1.0]], [[0.5, 0.5], [0.5, 0.5]])
        paths = [reg.save(model, "random_forest", {"role": r}, role=r)
                 for r in ("left", "right", "left", "left")]
        dirs = {str(p.parent) for p in paths}
        assert len(dirs) == 4                                  # nothing overwritten
        listed = reg.list_models()
        assert sorted(m["metrics"]["role"] for m in listed) == ["left", "left", "left", "right"]
        assert all(m["dir"] == Path(m["path"]).parent.name for m in listed)
        # newest is last, which the "newest model" lookups rely on
        assert listed[-1]["path"] == str(paths[-1])

    def test_listing_follows_creation_time_not_folder_names(self, tmp_path):
        # Two folders whose names sort the wrong way round: the one made first
        # must still be listed first (two saves in one millisecond sort by hand).
        from openmuscle.ml.registry import ModelRegistry
        reg = ModelRegistry(str(tmp_path / "reg"))
        for name, created_ns in (("z_first", 1_000), ("a_second", 2_000)):
            d = reg.base_dir / name
            d.mkdir()
            (d / "model.pkl").write_bytes(b"")
            (d / "metadata.json").write_text(json.dumps({"name": name, "created_ns": created_ns}))
        assert [m["dir"] for m in reg.list_models()] == ["z_first", "a_second"]
        # An old folder without the field still lists, by its own mtime.
        d = reg.base_dir / "legacy"
        d.mkdir()
        (d / "model.pkl").write_bytes(b"")
        (d / "metadata.json").write_text(json.dumps({"name": "legacy"}))
        assert {m["dir"] for m in reg.list_models()} == {"z_first", "a_second", "legacy"}

    def test_trained_left_then_right_get_two_folders_with_the_right_roles(self, tmp_path):
        from tests.test_train_checks import _rows, _write
        from openmuscle.ml.training import train_model
        from openmuscle.ml.registry import ModelRegistry
        _write(tmp_path / "two.csv", _rows(300, role="left") + _rows(300, role="right"))
        train_model(str(tmp_path / "two.csv"), n_estimators=5, role="left")
        train_model(str(tmp_path / "two.csv"), n_estimators=5, role="right")
        listed = ModelRegistry().list_models()
        assert len(listed) == 2
        assert sorted(m["metrics"]["role"] for m in listed) == ["left", "right"]
        assert listed[0]["dir"].endswith("_left") and listed[1]["dir"].endswith("_right")

    def test_train_output_writes_metadata_beside_the_pickle(self, tmp_path):
        from tests.test_train_checks import _rows, _write
        from openmuscle.ml.training import train_model
        _write(tmp_path / "d.csv", _rows(300, role="left") + _rows(300, role="right"))
        out_l = tmp_path / "out" / "model_left.pkl"
        out_r = tmp_path / "out" / "model_right.pkl"
        train_model(str(tmp_path / "d.csv"), n_estimators=5, role="left", output=str(out_l))
        train_model(str(tmp_path / "d.csv"), n_estimators=5, role="right", output=str(out_r))
        meta = json.loads((tmp_path / "out" / "metadata.json").read_text())
        assert meta["metrics"]["role"] in ("left", "right")
        assert "verdict" in meta and meta["verdict"]["headline"]
        # each pickle keeps ITS OWN metadata even though they share a folder
        left = json.loads((tmp_path / "out" / "model_left.metadata.json").read_text())
        right = json.loads((tmp_path / "out" / "model_right.metadata.json").read_text())
        assert left["metrics"]["role"] == "left" and right["metrics"]["role"] == "right"
        assert InferenceEngine(str(out_l)).metadata["metrics"]["role"] == "left"
        assert InferenceEngine(str(out_r)).metadata["metrics"]["role"] == "right"
        assert InferenceEngine(str(out_l)).n_features == 6

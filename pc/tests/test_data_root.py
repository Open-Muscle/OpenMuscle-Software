"""One data root (~/OpenMuscle) for captures, sessions and models."""

import os
from pathlib import Path

import pytest

from openmuscle import paths
from openmuscle.data.storage import CaptureWriter
from openmuscle.ml.registry import ModelRegistry
from openmuscle.web.state import AppState


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A clean HOME with no OPENMUSCLE_HOME override."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(home) + p[1:] if p.startswith("~") else p)
    paths.set_data_root(None)
    return home


def test_default_root_is_openmuscle_in_home(fake_home):
    assert paths.data_root() == fake_home / "OpenMuscle"
    assert paths.captures_dir() == fake_home / "OpenMuscle" / "captures"
    assert paths.sessions_dir() == fake_home / "OpenMuscle" / "sessions"
    assert paths.models_dir() == fake_home / "OpenMuscle" / "models"
    assert paths.exports_dir() == fake_home / "OpenMuscle" / "exports"
    assert paths.logs_dir() == fake_home / "OpenMuscle" / "logs"


def test_env_override_and_tilde_expansion(fake_home, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, "~/elsewhere")
    assert paths.data_root() == fake_home / "elsewhere"


def test_set_data_root_beats_env(fake_home, monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "from_env"))
    paths.set_data_root(tmp_path / "from_flag")
    assert paths.data_root() == tmp_path / "from_flag"
    paths.set_data_root(None)
    assert paths.data_root() == tmp_path / "from_env"


def test_ensure_data_dirs_creates_every_subfolder(fake_home):
    root = paths.ensure_data_dirs()
    for name in ("captures", "sessions", "models", "exports", "logs"):
        assert (root / name).is_dir()


def test_same_folders_from_three_working_directories(fake_home, tmp_path, monkeypatch):
    seen = set()
    for i in range(3):
        cwd = tmp_path / f"cwd{i}"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        state = AppState(udp_port=53960 + i, enable_discovery=False)
        w = CaptureWriter(matrix_rows=1, matrix_cols=1, label_count=1)
        w.close()
        seen.add((str(state.captures_dir), str(state.sessions_dir),
                  str(ModelRegistry().base_dir), str(w.path.parent)))
        # Nothing may be created in the launch directory.
        assert list(cwd.iterdir()) == []
    assert len(seen) == 1
    captures, sessions, models, writer_dir = seen.pop()
    root = fake_home / "OpenMuscle"
    assert Path(captures) == root / "captures"
    assert Path(sessions) == root / "sessions"
    assert Path(models) == root / "models"
    assert Path(writer_dir) == root / "captures"


def test_data_dir_override_moves_everything(tmp_path):
    paths.set_data_root(tmp_path / "mine")
    state = AppState(udp_port=53966, enable_discovery=False)
    assert state.captures_dir == tmp_path / "mine" / "captures"
    assert state.sessions_dir == tmp_path / "mine" / "sessions"
    assert ModelRegistry().base_dir == tmp_path / "mine" / "models"


def test_explicit_captures_dir_keeps_working(tmp_path):
    caps = tmp_path / "my_caps"
    state = AppState(udp_port=53967, captures_dir=str(caps), enable_discovery=False)
    assert state.captures_dir == caps
    # Sessions stay beside the captures folder (the pre-existing layout).
    assert state.sessions_dir == tmp_path / "sessions"


def test_storage_info_has_absolute_paths(tmp_path):
    paths.set_data_root(tmp_path / "root")
    state = AppState(udp_port=53968, enable_discovery=False)
    info = state.storage_info()
    for key in ("root", "captures", "sessions", "models", "exports", "logs"):
        assert os.path.isabs(info[key]), key
    assert info["root"] == str(tmp_path / "root")
    assert info["captures"] == str(tmp_path / "root" / "captures")


def test_api_storage_endpoint(tmp_path):
    # Call the route function directly (starlette's TestClient needs httpx, which
    # is not a dependency of this package).
    import asyncio
    from openmuscle.web.app import create_app

    paths.set_data_root(tmp_path / "root")
    app = create_app(udp_port=53969)
    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/storage")
    body = asyncio.run(route.endpoint())
    assert body["root"] == str(tmp_path / "root")
    assert body["captures"] == str(tmp_path / "root" / "captures")
    assert body["models"] == str(tmp_path / "root" / "models")


def test_legacy_cwd_data_triggers_hint(fake_home, tmp_path, monkeypatch):
    cwd = tmp_path / "old_project"
    merged = cwd / "data" / "raw" / "merged"
    merged.mkdir(parents=True)
    (merged / "capture_1.csv").write_text("x")
    monkeypatch.chdir(cwd)
    hint = paths.legacy_hint()
    assert hint is not None and "\n" not in hint
    assert str(merged) in hint and str(paths.data_root()) in hint


def test_no_hint_in_a_clean_directory(fake_home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert paths.legacy_hint() is None


def test_no_hint_when_cwd_data_is_the_root_itself(tmp_path, monkeypatch):
    # OPENMUSCLE_HOME pointed at <cwd>/data/raw would not be "legacy".
    root = tmp_path / "data" / "raw"
    paths.set_data_root(root)
    merged = root / "merged"
    merged.mkdir(parents=True)
    (merged / "a.csv").write_text("x")
    monkeypatch.chdir(tmp_path)
    assert paths.legacy_hint() is None

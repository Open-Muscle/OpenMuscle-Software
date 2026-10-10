"""Shared pytest fixtures."""

import pytest

from openmuscle import paths


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path_factory, monkeypatch):
    """Point the OpenMuscle data root at a throwaway folder for every test.

    Without this, anything that falls back to the default root (ModelRegistry(),
    CaptureWriter(None), AppState without captures_dir) would write into the
    real ~/OpenMuscle of whoever runs the tests.
    """
    root = tmp_path_factory.mktemp("om_home")
    monkeypatch.setenv(paths.ENV_HOME, str(root))
    paths.set_data_root(None)
    # The older profile-level bands file must not leak in from the real home.
    monkeypatch.setattr(paths, "legacy_devices_cache_path",
                        lambda: root.parent / "no_legacy_home" / "discovered_devices.json")
    yield root
    paths.set_data_root(None)


@pytest.fixture
def tiny_training_ok(monkeypatch):
    """Let the small synthetic recordings most trainer tests use through the
    pre-train data checks (the real minimums are 150 rows / 10 seconds, and a cell
    must move more than 5 counts to count as touching the arm)."""
    from openmuscle.ml import checks
    monkeypatch.setattr(checks, "MIN_ROWS", 8)
    monkeypatch.setattr(checks, "MIN_SECONDS", 0.0)
    # Synthetic sensors live in 0..1, far under the real 5-count flat threshold.
    monkeypatch.setattr(checks, "FLAT_RANGE_COUNTS", 0.0)

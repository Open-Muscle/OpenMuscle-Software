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
    yield root
    paths.set_data_root(None)

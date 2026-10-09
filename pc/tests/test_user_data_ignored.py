"""A recipient's own recordings can never be committed by accident, and the
sample files that used to live in the default captures folder have moved.

The first test copies the repository's .gitignore into a throwaway git repo,
records a take and a session with the data root INSIDE that clone, adds a
model folder, and asserts `git status` stays clean.
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from openmuscle import paths
from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION
from openmuscle.web.state import AppState

REPO = Path(__file__).resolve().parents[2]
HAVE_GIT = shutil.which("git") is not None


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=60)


def _band(t):
    matrix = [[100 + c for _ in range(4)] for c in range(15)]
    return OpenMusclePacket(version=CURRENT_VERSION, device_type="flexgrid",
                            device_id="fg-1", timestamp_ms=int(t * 1000),
                            data={"matrix": matrix, "rows": 4, "cols": 15},
                            receive_time=t)


@pytest.mark.skipif(not HAVE_GIT, reason="git not installed")
def test_recording_inside_a_clone_leaves_git_status_clean(tmp_path, monkeypatch):
    clone = tmp_path / "clone"
    clone.mkdir()
    assert _git(clone, "init", "-q").returncode == 0
    shutil.copy(REPO / ".gitignore", clone / ".gitignore")
    (clone / "README.md").write_text("x\n")
    _git(clone, "add", "-A")
    assert _git(clone, "-c", "user.email=t@example.org", "-c", "user.name=t",
                "commit", "-q", "-m", "init").returncode == 0

    # Data root inside the clone, as `openmuscle web --data-dir data` would do.
    root = clone / "data"
    monkeypatch.setenv(paths.ENV_HOME, str(root))
    paths.set_data_root(None)
    paths.ensure_data_dirs()
    s = AppState(udp_port=53993, captures_dir=str(root / "captures"),
                 enable_discovery=False)
    s.start_session(name="s", wearer="P01", arm="both")
    s._handle_packet(_band(time.time()))
    s.start_recording(sensor_device_id="fg-1", label_device_id="", filename="t")
    s.stop_recording()
    s.end_session()
    model = root / "models" / "random_forest_x"
    model.mkdir(parents=True)
    (model / "model.pkl").write_bytes(b"\x80\x04.")
    (model / "metadata.json").write_text("{}")
    (root / "logs" / "server.log").write_text("hi\n")
    (root / "exports").mkdir(exist_ok=True)
    (root / "exports" / "e.zip").write_bytes(b"PK")
    # Sidecars wherever a folder was pinned by hand.
    pinned = clone / "somewhere"
    pinned.mkdir()
    for name in ("a.sensor.jsonl", "a.label.jsonl", "a.meta.json",
                 "a.labels.schema.json"):
        (pinned / name).write_text("{}\n")

    st = _git(clone, "status", "--porcelain", "--untracked-files=all")
    assert st.stdout.strip() == "", st.stdout
    # The take and the session really were written (so the check above means something).
    assert list((root / "captures").glob("t*.csv"))
    assert list((root / "sessions").glob("*.json"))

    for rel in ("data/captures/t.csv", "data/sessions/x.json",
                "data/models/m/model.pkl", "x/y.sensor.jsonl", "x/y.meta.json"):
        r = _git(clone, "check-ignore", "-q", rel)
        assert r.returncode == 0, rel


@pytest.mark.skipif(not HAVE_GIT, reason="git not installed")
def test_repo_source_and_samples_are_not_ignored():
    for rel in ("pc/src/openmuscle/data/dataset.py", "pc/data/README.md",
                "data/samples/legacy/README.md"):
        r = _git(REPO, "check-ignore", "-q", rel)
        assert r.returncode == 1, rel + " must not be ignored"


@pytest.mark.skipif(not HAVE_GIT, reason="git not installed")
def test_legacy_samples_moved_out_of_the_default_folder():
    out = _git(REPO, "ls-files", "data").stdout.splitlines()
    assert not [p for p in out if p.startswith("data/raw/merged/")]
    moved = [p for p in out if p.startswith("data/samples/legacy/")
             and p.endswith(".csv")]
    assert len(moved) == 20
    assert "data/samples/legacy/README.md" in out

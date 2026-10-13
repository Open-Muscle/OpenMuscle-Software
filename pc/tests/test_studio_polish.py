"""Studio polish: the launchers say which address they open and prefer the port
the last run used; a session left open by a stopped run is closed and marked;
participant codes show as typed; the folded result card is built from the same
summary the unit tests check."""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner

from openmuscle import cli, paths
from openmuscle.web.state import AppState

REPO = Path(__file__).resolve().parents[2]
INSTALLERS = REPO / "installers"
STATIC = Path(__file__).resolve().parents[1] / "src" / "openmuscle" / "web" / "static"


# ---------------------------------------------------------------- last port ----

def test_last_port_round_trip_and_bad_files(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths.set_data_root(None)
    assert paths.read_last_port() is None                      # nothing yet
    paths.write_last_port(8003)
    assert paths.last_port_path() == tmp_path / "home" / "logs" / "last-port.txt"
    assert paths.read_last_port() == 8003
    for junk in ("", "abc", "80x3", "0", "70000", "-5", "8003; rm -rf"):
        paths.last_port_path().write_text(junk, encoding="ascii")
        assert paths.read_last_port() is None, junk


def test_write_last_port_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the data folder should be")
    monkeypatch.setenv(paths.ENV_HOME, str(blocker))
    paths.set_data_root(None)
    paths.write_last_port(8000)                                # must not raise
    assert paths.read_last_port() is None


def test_web_records_the_port_it_serves_on(tmp_path, monkeypatch):
    home = tmp_path / "om"
    monkeypatch.setenv(paths.ENV_HOME, str(home))
    paths.set_data_root(None)
    monkeypatch.setattr(cli, "_port_free", lambda host, port: port != 8000)
    monkeypatch.setattr(cli, "_udp_port_status", lambda port: (True, ""))
    from openmuscle import netutil
    monkeypatch.setattr(netutil, "find_running_instance", lambda *a, **k: None)
    import openmuscle.web.app as webapp
    monkeypatch.setattr(webapp, "serve", lambda **kw: None)
    result = CliRunner().invoke(cli.main, ["web", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "http://localhost:8001" in result.output           # 8000 was busy
    assert paths.read_last_port() == 8001
    assert (home / "logs" / "last-port.txt").read_text().strip() == "8001"


# ------------------------------------------------------- launcher text checks ----

def test_launchers_name_the_address_and_read_the_recorded_port():
    bat = (INSTALLERS / "windows" / "OpenMuscle.bat").read_text(encoding="ascii")
    mac = (INSTALLERS / "macos" / "OpenMuscle.command").read_text(encoding="ascii")
    linux = (INSTALLERS / "linux" / "install.sh").read_text(encoding="ascii")
    for text in (bat, mac, linux):
        assert "last-port.txt" in text
        assert "already running at http://localhost:" in text
    # The recorded port is probed before the 8000 to 8010 scan.
    assert bat.index("call :probe %LASTPORT%") < bat.index("for /l %%P in (8000,1,8010)")
    assert mac.index('ports="$last"') < mac.index('ports="$ports $p"')
    assert linux.index('ports="\\$last"') < linux.index('ports="\\$ports \\$p"')


# ------------------------------------------------ launchers actually run ----

class _Mode(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"app": "openmuscle", "version": "test", "debug": False}).encode()
        self.send_response(200 if self.path == "/api/mode" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_openmuscle():
    """A server on a free port (above 8010) that answers /api/mode like OpenMuscle."""
    srv = HTTPServer(("127.0.0.1", 0), _Mode)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _need(tool):
    if not shutil.which(tool):
        pytest.skip(tool + " not available")


def _unix_sandbox(tmp_path, opener):
    """An OPENMUSCLE_HOME with a stub openmuscle that only prints, and a PATH
    whose first folder holds a stub browser opener that records its argument."""
    home = tmp_path / "om"
    exe = home / "app" / "bin" / "openmuscle"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\necho STUB-STARTED\nexit 0\n", newline="\n")
    exe.chmod(0o755)
    shims = tmp_path / "shims"
    shims.mkdir()
    log = tmp_path / "opened.txt"
    stub = shims / opener
    stub.write_text('#!/bin/sh\necho "$1" >> "%s"\n' % log.as_posix(), newline="\n")
    stub.chmod(0o755)
    (home / "logs").mkdir()
    env = dict(os.environ, OPENMUSCLE_HOME=str(home), OPENMUSCLE_NO_PAUSE="1",
               PATH=str(shims) + os.pathsep + os.environ["PATH"])
    env.pop("OPENMUSCLE_DRY_RUN", None)
    return home, env, log


def _run_sh(script, env, cwd):
    return subprocess.run([shutil.which("bash"), str(script)], capture_output=True,
                          text=True, env=env, timeout=60, stdin=subprocess.DEVNULL,
                          cwd=str(cwd))


def _linux_wrapper(tmp_path, home):
    """The openmuscle-start the Linux installer writes, filled in for `home`."""
    text = (INSTALLERS / "linux" / "install.sh").read_text(encoding="ascii")
    m = re.search(r'cat > "\$WRAPPER" <<EOF\n(.*?)\nEOF\n', text, re.S)
    assert m, "launcher heredoc not found"
    body = (m.group(1)
            .replace("$ROOT", str(home).replace("\\", "/"))
            .replace("$APP", str(home / "app").replace("\\", "/"))
            .replace("\\$", "$"))
    out = tmp_path / "openmuscle-start"
    out.write_text(body + "\n", encoding="ascii", newline="\n")
    out.chmod(0o755)
    return out


def _unix_script(which, tmp_path, home):
    if which == "macos":
        return INSTALLERS / "macos" / "OpenMuscle.command"
    return _linux_wrapper(tmp_path, home)


@pytest.mark.parametrize("which", ["macos", "linux"])
def test_unix_launcher_reopens_the_recorded_port(which, tmp_path, fake_openmuscle):
    _need("bash")
    _need("curl")
    home, env, log = _unix_sandbox(tmp_path, "open" if which == "macos" else "xdg-open")
    (home / "logs" / "last-port.txt").write_text("%d\n" % fake_openmuscle)
    r = _run_sh(_unix_script(which, tmp_path, home), env, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    url = "http://localhost:%d" % fake_openmuscle
    assert "already running at " + url in r.stdout
    assert "STUB-STARTED" not in r.stdout                      # no second copy
    assert log.read_text().strip() == url


@pytest.mark.parametrize("which", ["macos", "linux"])
def test_unix_launcher_ignores_a_damaged_port_file(which, tmp_path):
    _need("bash")
    _need("curl")
    home, env, log = _unix_sandbox(tmp_path, "open" if which == "macos" else "xdg-open")
    (home / "logs" / "last-port.txt").write_text("80; echo HACKED\n")
    r = _run_sh(_unix_script(which, tmp_path, home), env, tmp_path)
    assert "HACKED" not in r.stdout + r.stderr
    # Nothing answers on 8000 to 8010 in the sandbox unless the machine runs a
    # real copy; either way the launcher must not choke on the file.
    assert "STUB-STARTED" in r.stdout or "already running at" in r.stdout


def _windows_sandbox(tmp_path, port_text):
    home = tmp_path / "om"
    exe = home / "app" / "Scripts" / "openmuscle.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")                                       # only has to exist
    (home / "logs").mkdir()
    (home / "logs" / "last-port.txt").write_text(port_text)
    raw = (INSTALLERS / "windows" / "OpenMuscle.bat").read_bytes()
    opener = b'start "" "http://localhost:%RUNNING%"'
    launch = b'"%EXE%" web %*'
    assert opener in raw and launch in raw
    # A copy that opens no browser and starts no program: the stand-in exe is
    # empty, and running it would leave the real launcher at its "press any
    # key" prompt (which a CI runner never answers).
    bat = tmp_path / "OpenMuscle.bat"
    bat.write_bytes(raw.replace(opener, b"echo OPENED http://localhost:%RUNNING%")
                       .replace(launch, b"echo LAUNCH %EXE% web %*"))
    env = dict(os.environ, OPENMUSCLE_HOME=str(home))
    env.pop("OPENMUSCLE_DRY_RUN", None)
    return bat, env


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="cmd.exe only")
def test_windows_launcher_reopens_the_recorded_port(tmp_path, fake_openmuscle):
    _need("curl")
    bat, env = _windows_sandbox(tmp_path, "%d\n" % fake_openmuscle)
    r = subprocess.run(["cmd.exe", "/c", str(bat)], capture_output=True, text=True,
                       env=env, timeout=90, stdin=subprocess.DEVNULL)
    url = "http://localhost:%d" % fake_openmuscle
    assert r.returncode == 0, r.stdout + r.stderr
    assert "already running at " + url in r.stdout
    assert "OPENED " + url in r.stdout


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="cmd.exe only")
def test_windows_launcher_ignores_a_damaged_port_file(tmp_path):
    _need("curl")
    bat, env = _windows_sandbox(tmp_path, "80&echo HACKED\n")
    r = subprocess.run(["cmd.exe", "/c", str(bat)], capture_output=True, text=True,
                       env=env, timeout=90, stdin=subprocess.DEVNULL)
    assert "HACKED" not in r.stdout
    assert r.returncode == 0, r.stdout + r.stderr
    assert "already running" not in r.stdout                  # nothing answered the probes
    assert "LAUNCH " in r.stdout                              # so it went on to start the app


# -------------------------------------------------- unfinished sessions ----

def _state(tmp_path, port=53991):
    caps = tmp_path / "captures"
    caps.mkdir(exist_ok=True)
    return AppState(udp_port=port, captures_dir=str(caps), enable_discovery=False)


def test_a_session_left_open_is_closed_and_marked(tmp_path):
    s = _state(tmp_path)
    opened = s.start_session(name="Morning", wearer="P-07")
    sid = opened["id"]
    s.active_session = None                                    # the run stopped, no End click
    assert s.get_session(sid)["ended_at"] is None

    fresh = _state(tmp_path, port=53992)                       # the next run
    assert fresh.close_orphaned_sessions() == [sid]
    got = fresh.get_session(sid)
    assert got["closed_on_restart"] is True
    assert got["ended_at"] is not None and got["ended_at"] >= opened["started_at"]
    assert got["name"] == "Morning" and got["wearer"] == "P-07"
    assert fresh.active_session is None                        # never revived as active
    assert any(x["id"] == sid for x in fresh.list_sessions())
    assert fresh.close_orphaned_sessions() == []               # idempotent
    assert any("still open" in e["message"] for e in fresh.log_buffer.entries())


def test_an_ended_session_is_left_alone(tmp_path):
    s = _state(tmp_path)
    sid = s.start_session(name="Done")["id"]
    s.end_session()
    before = s.get_session(sid)
    assert _state(tmp_path, port=53993).close_orphaned_sessions() == []
    after = s.get_session(sid)
    assert after == before and "closed_on_restart" not in after


def test_close_orphaned_sessions_skips_a_missing_folder_and_bad_files(tmp_path):
    s = _state(tmp_path)
    assert s.close_orphaned_sessions() == []                   # no sessions folder yet
    assert not (tmp_path / "sessions").exists()                # and none was created
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "broken.json").write_text("{not json")
    (tmp_path / "sessions" / "list.json").write_text("[1, 2]")
    assert s.close_orphaned_sessions() == []


def test_ending_a_session_clears_the_mark_and_edits_cannot_set_it(tmp_path):
    s = _state(tmp_path)
    sid = s.start_session(name="X")["id"]
    s.update_session(sid, {"closed_on_restart": True, "notes": "hi"})
    assert "closed_on_restart" not in s.get_session(sid)
    assert s.get_session(sid)["notes"] == "hi"


def test_start_closes_old_sessions():
    import inspect
    assert "close_orphaned_sessions" in inspect.getsource(AppState.start)


# ---------------------------------------------- participant shown as typed ----

@pytest.mark.usefixtures("tiny_training_ok")
def test_trained_model_keeps_the_typed_code_for_display(tmp_path, monkeypatch):
    from .test_train_session_stamp import _write_v2_csv, _train_left
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    s = _state(tmp_path, port=53994)
    _write_v2_csv(s.captures_dir / "take1.csv")
    res = _train_left(s, session_wearer="P-07")
    meta = json.loads((Path(res["model_path"]).parent / "metadata.json").read_text())
    assert meta["participant"] == "p-07"                       # the key stays lower-case
    assert meta["participant_label"] == "P-07"                 # the label is as typed
    row = s.list_models()[0]
    assert row["participant"] == "p-07" and row["participant_label"] == "P-07"


@pytest.mark.usefixtures("tiny_training_ok")
def test_a_model_without_a_label_falls_back(tmp_path, monkeypatch):
    from .test_train_session_stamp import _write_v2_csv, _train_left
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    s = _state(tmp_path, port=53995)
    _write_v2_csv(s.captures_dir / "take1.csv")
    res = _train_left(s, session_wearer="P-07")
    mp = Path(res["model_path"]).parent / "metadata.json"
    meta = json.loads(mp.read_text())
    meta.pop("participant_label")                              # a model from before the label
    meta["wearer"] = "P-07"
    mp.write_text(json.dumps(meta))
    assert s.list_models()[0]["participant_label"] == "P-07"   # typed wearer, same code
    meta.pop("wearer")
    mp.write_text(json.dumps(meta))
    assert s.list_models()[0]["participant_label"] == "p-07"   # only the key is left


# ------------------------------------------------ result card uses the summary ----

def test_post_stop_card_is_built_from_saved_summary():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    body = app[app.index("function showPostStopCard"):app.index("recordBtn.onclick")]
    assert "OMRecStatus.savedSummary(result)" in body
    assert "sum.actions" in body and "sum.headline" in body
    assert "OMRecStatus.savedActions" not in body

"""Where are my files: /api/storage details and the Open folder (reveal) route."""

import shutil
import sys
from collections import namedtuple
from pathlib import Path

import pytest

from openmuscle import paths
from openmuscle.web import app as app_mod
from openmuscle.web.app import create_app

from .asgi_helper import call_json

LAN_CLIENT = ("192.168.1.50", 51234)


@pytest.fixture
def popen(monkeypatch):
    """Record what would be launched instead of opening a window."""
    calls = []
    monkeypatch.setattr(app_mod.subprocess, "Popen", lambda argv, *a, **k: calls.append(argv))
    return calls


def _app(tmp_path, **kw):
    paths.set_data_root(tmp_path / "root")
    return create_app(udp_port=53971, **kw)


def test_storage_reports_paths_and_free_space(tmp_path):
    app = _app(tmp_path)
    status, info = call_json(app, "GET", "/api/storage")
    assert status == 200
    for key in ("root", "captures", "sessions", "models", "exports", "logs"):
        assert Path(info[key]).is_absolute(), key
    assert info["root"] == str(tmp_path / "root")
    assert isinstance(info["free_bytes"], int) and info["free_bytes"] > 0
    assert info["low_space"] is False
    assert info["reveal_enabled"] is True


def test_low_disk_space_is_flagged(tmp_path, monkeypatch):
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(100 * 1024 ** 3, 100 * 1024 ** 3 - 500 * 1024 ** 2, 500 * 1024 ** 2))
    app = _app(tmp_path)
    _s, info = call_json(app, "GET", "/api/storage")
    assert info["low_space"] is True and info["free_bytes"] == 500 * 1024 ** 2
    assert info["low_space_bytes"] == 1024 ** 3


def test_reveal_is_off_for_other_devices_and_in_debug(tmp_path):
    app = _app(tmp_path)
    _s, info = call_json(app, "GET", "/api/storage", client=LAN_CLIENT)
    assert info["reveal_enabled"] is False
    assert info["root"]                                  # the path is still given as text
    dbg = _app(tmp_path, debug=True)
    _s, info = call_json(dbg, "GET", "/api/storage")
    assert info["reveal_enabled"] is False and info["captures"]


def test_reveal_refuses_non_loopback_clients(tmp_path, popen):
    app = _app(tmp_path)
    status, body = call_json(app, "POST", "/api/reveal", body={}, client=LAN_CLIENT)
    assert status == 403
    assert popen == []
    # --debug is LAN-open, so even this computer is refused there.
    dbg = _app(tmp_path, debug=True)
    assert call_json(dbg, "POST", "/api/reveal", body={})[0] == 403
    assert popen == []


@pytest.mark.parametrize("client", [("127.0.0.1", 1), ("::1", 1), ("::ffff:127.0.0.1", 1),
                                    ("localhost", 1)])
def test_reveal_accepts_loopback_in_any_spelling(tmp_path, popen, client):
    app = _app(tmp_path)
    status, body = call_json(app, "POST", "/api/reveal", body={}, client=client)
    assert status == 200, body
    assert len(popen) == 1


def test_reveal_returns_absolute_paths_for_each_folder(tmp_path, popen):
    app = _app(tmp_path)
    for folder in ("root", "captures", "sessions", "models", "exports"):
        status, body = call_json(app, "POST", "/api/reveal", body={"folder": folder})
        assert status == 200, (folder, body)
        assert Path(body["opened"]).is_absolute()
        assert Path(body["opened"]).is_dir()
        assert body["opened"].endswith(folder if folder != "root" else "root")
    assert len(popen) == 5                                # models existed only after the call


def test_reveal_rejects_unknown_folders_and_unknown_captures(tmp_path, popen):
    app = _app(tmp_path)
    assert call_json(app, "POST", "/api/reveal", body={"folder": "../etc"})[0] == 400
    assert call_json(app, "POST", "/api/reveal", body={"name": "nope.csv"})[0] == 404
    assert popen == []


def test_reveal_highlights_a_capture(tmp_path, popen, monkeypatch):
    app = _app(tmp_path)
    (app.state.app_state.captures_dir / "a.csv").write_text("x")
    monkeypatch.setattr(sys, "platform", "darwin")
    status, body = call_json(app, "POST", "/api/reveal", body={"name": "a.csv"})
    assert status == 200 and body["selected"] is True
    assert popen[0][:2] == ["open", "-R"] and Path(popen[0][2]).name == "a.csv"


@pytest.mark.parametrize("platform, tools, expected_head", [
    ("win32", {}, ["explorer"]),
    ("darwin", {}, ["open"]),
    ("linux", {"xdg-open": "/usr/bin/xdg-open"}, ["/usr/bin/xdg-open"]),
    ("linux", {"gio": "/usr/bin/gio"}, ["/usr/bin/gio", "open"]),
])
def test_opener_command_per_platform(tmp_path, popen, monkeypatch, platform, tools,
                                     expected_head):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(app_mod.shutil, "which", lambda name: tools.get(name))
    folder = tmp_path / "f"
    folder.mkdir()
    app_mod._reveal_path_in_file_manager(folder, select_file=False)
    argv = popen[0]
    assert argv[:len(expected_head)] == expected_head
    assert Path(argv[-1]).is_absolute() and Path(argv[-1]) == folder.resolve()


def test_linux_without_an_opener_gives_a_plain_error(tmp_path, popen, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(app_mod.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="Open this folder yourself"):
        app_mod._reveal_path_in_file_manager(tmp_path, select_file=False)
    assert popen == []
    app = _app(tmp_path)
    status, body = call_json(app, "POST", "/api/reveal", body={})
    assert status == 500 and "xdg-utils" in body["detail"]

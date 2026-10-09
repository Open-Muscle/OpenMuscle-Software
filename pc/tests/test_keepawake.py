"""Keep the computer awake while the server runs (ctypes and caffeinate stubbed)."""

import pytest

from openmuscle import keepawake
from openmuscle.keepawake import ES_CONTINUOUS, ES_SYSTEM_REQUIRED, KeepAwake


class FakeKernel32:
    def __init__(self, result=1):
        self.calls = []
        self.result = result

    def SetThreadExecutionState(self, flags):
        self.calls.append(flags)
        return self.result


def test_windows_holds_then_releases(monkeypatch):
    k = FakeKernel32()
    monkeypatch.setattr(keepawake, "_kernel32", lambda: k)
    ka = KeepAwake(platform="win32")
    assert ka.start() is True
    assert ka.active and ka.method == "windows"
    assert k.calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED]
    ka.stop()
    assert not ka.active
    assert k.calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS]


def test_windows_start_and_stop_are_idempotent(monkeypatch):
    k = FakeKernel32()
    monkeypatch.setattr(keepawake, "_kernel32", lambda: k)
    ka = KeepAwake(platform="win32")
    ka.start()
    ka.start()
    ka.stop()
    ka.stop()
    assert len(k.calls) == 2


def test_windows_refusal_leaves_it_inactive(monkeypatch):
    monkeypatch.setattr(keepawake, "_kernel32", lambda: FakeKernel32(result=0))
    ka = KeepAwake(platform="win32")
    assert ka.start() is False
    assert not ka.active and ka.method == ""
    ka.stop()           # harmless


def test_windows_without_ctypes_windll_never_raises(monkeypatch):
    def boom():
        raise AttributeError("no windll")

    monkeypatch.setattr(keepawake, "_kernel32", boom)
    ka = KeepAwake(platform="win32")
    assert ka.start() is False


def test_linux_is_a_no_op(monkeypatch):
    def boom():
        raise AssertionError("must not touch ctypes on linux")

    monkeypatch.setattr(keepawake, "_kernel32", boom)
    ka = KeepAwake(platform="linux")
    assert ka.start() is False
    assert not ka.active
    ka.stop()


def test_macos_uses_caffeinate_tied_to_this_process(monkeypatch):
    import os
    started = {}

    class FakeProc:
        terminated = False

        def terminate(self):
            FakeProc.terminated = True

    def fake_popen(args, **kw):
        started["args"] = args
        return FakeProc()

    monkeypatch.setattr(keepawake.shutil, "which", lambda n: "/usr/bin/caffeinate")
    monkeypatch.setattr(keepawake.subprocess, "Popen", fake_popen)
    ka = KeepAwake(platform="darwin")
    assert ka.start() is True
    assert started["args"] == ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())]
    ka.stop()
    assert FakeProc.terminated and not ka.active


def test_macos_without_caffeinate_is_a_no_op(monkeypatch):
    monkeypatch.setattr(keepawake.shutil, "which", lambda n: None)
    ka = KeepAwake(platform="darwin")
    assert ka.start() is False and not ka.active


@pytest.mark.skipif(not keepawake.sys.platform.startswith("win"),
                    reason="real SetThreadExecutionState is Windows only")
def test_windows_real_call_succeeds_and_releases():
    ka = KeepAwake()
    assert ka.start() is True
    ka.stop()
    assert not ka.active


# ---- wired into serve() and the CLI ------------------------------------------

def _stub_serve_env(monkeypatch, record, uvicorn_run):
    import uvicorn
    import openmuscle.web.app as webapp
    monkeypatch.setattr(uvicorn, "run", uvicorn_run)
    monkeypatch.setattr(webapp, "create_app", lambda **kw: object())

    class Spy(KeepAwake):
        def start(self):
            record.append("start")
            return True

        def stop(self):
            record.append("stop")

    monkeypatch.setattr(keepawake, "KeepAwake", Spy)
    return webapp


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_serve_holds_awake_only_while_running(monkeypatch):
    record = []
    webapp = _stub_serve_env(monkeypatch, record, lambda *a, **k: record.append("run"))
    webapp.serve(port=_free_port(), udp_port=_free_port())
    assert record == ["start", "run", "stop"]


def test_serve_releases_even_when_the_server_dies(monkeypatch):
    record = []

    def crash(*a, **k):
        raise RuntimeError("server died")

    webapp = _stub_serve_env(monkeypatch, record, crash)
    with pytest.raises(RuntimeError):
        webapp.serve(port=_free_port(), udp_port=_free_port())
    assert record == ["start", "stop"]


def test_serve_opt_out(monkeypatch):
    record = []
    webapp = _stub_serve_env(monkeypatch, record, lambda *a, **k: record.append("run"))
    webapp.serve(port=_free_port(), udp_port=_free_port(), keep_awake=False)
    assert record == ["run"]

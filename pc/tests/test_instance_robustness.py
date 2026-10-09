"""A second `openmuscle web`, busy ports, and the discovery/diagnose routes.

The subprocess test starts two real servers on throwaway ports; everything else
stubs serve() so nothing listens.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import openmuscle
from openmuscle import cli, netutil
from openmuscle.web import app as webapp
from tests.test_netutil import _serve_json, hold_udp_port


def _free_tcp():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _free_udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---- a second instance is recognised ---------------------------------------------

@pytest.mark.parametrize("explicit", [True, False])
def test_pick_port_names_a_running_openmuscle(explicit):
    srv = _serve_json('{"debug": false, "app": "openmuscle"}')
    port = srv.server_address[1]
    try:
        with pytest.raises(click.ClickException) as ei:
            cli._pick_port("127.0.0.1", port, explicit=explicit)
        msg = ei.value.format_message()
        assert "OpenMuscle is already running" in msg
        assert f"http://localhost:{port}" in msg
        assert msg.count(". ") == 0 and "\n" not in msg     # one sentence
    finally:
        srv.shutdown()
        srv.server_close()


def test_busy_udp_data_port_stops_web_before_it_starts(monkeypatch):
    monkeypatch.setattr(cli, "_port_free", lambda h, p: True)
    monkeypatch.setattr(cli, "_udp_port_status", lambda p: (
        (False, f"UDP port {p} is already in use, probably by another OpenMuscle "
                f"window or another program.") if p == 3141 else (True, "")))
    monkeypatch.setattr(webapp, "serve",
                        lambda **kw: pytest.fail("serve() must not be reached"))
    res = CliRunner().invoke(cli.main, ["web", "--no-browser"])
    assert res.exit_code != 0
    assert "UDP port 3141 is already in use" in res.output
    assert "--udp-port" in res.output
    assert "Traceback" not in res.output


def test_busy_beacon_port_is_a_note_not_a_stop(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_port_free", lambda h, p: True)
    monkeypatch.setattr(cli, "_udp_port_status", lambda p: (
        (False, "UDP port 3140 is already in use.") if p == 3140 else (True, "")))
    monkeypatch.setattr(webapp, "serve", lambda **kw: seen.update(kw))
    res = CliRunner().invoke(cli.main, ["web", "--no-browser"])
    assert res.exit_code == 0, res.output
    assert "Note: UDP port 3140 is already in use." in res.output
    assert "openmuscle doctor" in res.output
    assert seen["announce_port"] == 3140


def test_serve_refuses_a_taken_port_before_starting_anything(monkeypatch):
    hold = socket.socket()
    netutil.make_exclusive(hold)
    hold.bind(("127.0.0.1", 0))
    hold.listen(1)
    port = hold.getsockname()[1]
    monkeypatch.setattr(webapp, "create_app",
                        lambda **kw: pytest.fail("no app may be built"))
    try:
        with pytest.raises(webapp.ServerStartError) as ei:
            webapp.serve(port=port, udp_port=_free_udp())
        assert f"Port {port} is already in use" in str(ei.value)
    finally:
        hold.close()


def test_web_turns_a_start_error_into_a_clean_exit(monkeypatch):
    monkeypatch.setattr(cli, "_port_free", lambda h, p: True)
    monkeypatch.setattr(cli, "_udp_port_status", lambda p: (True, ""))

    def refuse(**kw):
        raise webapp.ServerStartError("Port 8000 is already in use, so stop.")

    monkeypatch.setattr(webapp, "serve", refuse)
    res = CliRunner().invoke(cli.main, ["web", "--no-browser"])
    assert res.exit_code != 0
    assert "Port 8000 is already in use" in res.output
    assert "Traceback" not in res.output


def test_keep_awake_flag_reaches_serve(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_port_free", lambda h, p: True)
    monkeypatch.setattr(cli, "_udp_port_status", lambda p: (True, ""))
    monkeypatch.setattr(webapp, "serve", lambda **kw: seen.append(kw))
    on = CliRunner().invoke(cli.main, ["web", "--no-browser"])
    off = CliRunner().invoke(cli.main, ["web", "--no-browser", "--no-keep-awake"])
    assert seen[0]["keep_awake"] is True and seen[1]["keep_awake"] is False
    if sys.platform.startswith(("win", "darwin")):
        assert "kept awake" in on.output and "lid" in on.output
        assert "kept awake" not in off.output


def test_receive_reports_a_busy_port_plainly():
    s, port = hold_udp_port()
    try:
        from openmuscle.receiver.udp_listener import UDPListener
        with pytest.raises(Exception) as ei:
            UDPListener(port=port, quiet=True, strict=True).start()
        assert "already in use" in str(ei.value)
        res = CliRunner().invoke(cli.main, ["receive", "--port", str(port)])
        assert res.exit_code != 0
        assert "already in use" in res.output and "Traceback" not in res.output
    finally:
        s.close()


# ---- the real thing: two servers -----------------------------------------------------

def _kill_tree(proc):
    if proc.poll() is not None:
        return
    if sys.platform.startswith("win"):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        pass


def _web_args(http, udp, ann, extra=()):
    return [sys.executable, "-m", "openmuscle", "web", "--host", "127.0.0.1",
            "--port", str(http), "--udp-port", str(udp), "--announce-port", str(ann),
            "--no-browser", *extra]


def test_second_instance_exits_fast_with_one_sentence_and_leaves_no_orphan(tmp_path):
    src = str(Path(openmuscle.__file__).resolve().parent.parent)
    env = dict(os.environ, PYTHONPATH=src, OPENMUSCLE_HOME=str(tmp_path / "home"))
    http, udp, ann = _free_tcp(), _free_udp(), _free_udp()
    first = subprocess.Popen(_web_args(http, udp, ann), env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 60
        info = None
        while time.time() < deadline and info is None:
            assert first.poll() is None, "first instance died"
            info = netutil.find_running_instance(http, timeout=1.0)
            time.sleep(0.3)
        assert info and info["app"] == "openmuscle"

        # Same HTTP port: recognised as OpenMuscle, one sentence, non-zero, quick.
        t0 = time.time()
        second = subprocess.run(_web_args(http, udp, ann), env=env, timeout=30,
                                capture_output=True, text=True)
        took = time.time() - t0
        assert second.returncode != 0
        assert took < 3.0, took
        err = (second.stdout + second.stderr).strip()
        assert "OpenMuscle is already running at http://localhost:%d" % http in err
        assert "Traceback" not in err and len(err.splitlines()) == 1

        # Other HTTP port, same UDP data port: caught and named before starting.
        t0 = time.time()
        third = subprocess.run(_web_args(_free_tcp(), udp, ann), env=env, timeout=30,
                               capture_output=True, text=True)
        assert third.returncode != 0 and time.time() - t0 < 3.0
        err = third.stdout + third.stderr
        assert "UDP port %d is already in use" % udp in err
        assert "Traceback" not in err

        # The first one was not disturbed.
        assert netutil.find_running_instance(http, timeout=2.0) is not None
    finally:
        _kill_tree(first)
    # Ports are released once it is gone (no orphan server holding them).
    deadline = time.time() + 10
    while time.time() < deadline and not netutil.tcp_port_free("127.0.0.1", http):
        time.sleep(0.2)
    assert netutil.tcp_port_free("127.0.0.1", http)
    assert netutil.udp_bind_check(udp)[0]


# ---- routes ------------------------------------------------------------------------------

@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """create_app with a quiet network: no real broadcast, ssid fixed."""
    from openmuscle.discovery.manager import DiscoveryManager
    from openmuscle import doctor
    monkeypatch.setattr(DiscoveryManager, "broadcast_targets", lambda self: [])
    monkeypatch.setattr(netutil, "wifi_ssid", lambda: "HomeNet")
    monkeypatch.setattr(doctor, "_firewall_probe", lambda plat: {})
    monkeypatch.setattr(doctor, "_internet_probe", lambda: {})
    import openmuscle.discovery.manager as manager_module
    monkeypatch.setattr(manager_module, "default_cache_path",
                        lambda: str(tmp_path / "cache.json"))
    app = webapp.create_app(udp_port=_free_udp(), announce_port=_free_udp(),
                            captures_dir=str(tmp_path / "caps"), http_port=8000)
    app.state.app_state.discovery.cache_path = str(tmp_path / "cache.json")
    return app


def _route(app, path, method="GET"):
    return next(r for r in app.routes if getattr(r, "path", "") == path
                and method in getattr(r, "methods", {method}))


def _json(resp):
    return json.loads(resp.body) if hasattr(resp, "body") else resp


def test_mode_names_the_app(app_env):
    out = asyncio.run(_route(app_env, "/api/mode").endpoint())
    assert out["app"] == "openmuscle"
    assert out["debug"] is False and "version" in out


def test_discovery_stays_a_plain_list_and_detail_adds_health(app_env):
    state = app_env.state.app_state
    state.discovery.on_announce({"v": "1.0", "type": "announce", "id": "flexgrid-x1",
                                 "dev": "flexgrid"}, "10.0.0.23")
    route = _route(app_env, "/api/discovery")
    plain = _json(asyncio.run(route.endpoint(detail=False)))
    assert isinstance(plain, list) and plain[0]["device_id"] == "flexgrid-x1"
    detail = _json(asyncio.run(route.endpoint(detail=True)))
    assert detail["devices"][0]["device_id"] == "flexgrid-x1"
    assert "pc_host" in detail and "interface" in detail
    assert set(detail["listener_ok"]) == {"announce", "data"}
    status = _json(asyncio.run(_route(app_env, "/api/discovery/status").endpoint()))
    assert status["ports"]["data"] == state.udp_port
    assert status["data_listener"]["port"] == state.udp_port


def test_discover_route_returns_found_and_devices(app_env):
    out = asyncio.run(_route(app_env, "/api/discovery/discover", "POST").endpoint())
    assert out["found"] == [] and out["devices"] == []


def test_scan_route_passes_prefix_and_cap(app_env):
    state = app_env.state.app_state
    seen = {}

    def fake_scan(base, start, end, timeout, workers, background, prefix, max_hosts):
        seen.update(prefix=prefix, max_hosts=max_hosts)
        return ["flexgrid-z"]

    state.discovery.scan_subnet = fake_scan
    route = _route(app_env, "/api/discovery/scan", "POST")
    body = route.endpoint.__annotations__["body"](prefix=16, max_hosts=999999)
    out = asyncio.run(route.endpoint(body))
    assert seen == {"prefix": 16, "max_hosts": 1024}          # capped
    assert out["found"] == ["flexgrid-z"]
    assert route.summary == "Search my network"


def test_diagnose_route_returns_the_contract(app_env):
    out = _json(asyncio.run(_route(app_env, "/api/discovery/diagnose").endpoint(
        discover=False)))
    assert {"ok", "checks", "udp_port", "bound_host", "devices_seen",
            "last_announce_s", "firewall"} <= set(out)
    assert out["udp_port"] == app_env.state.app_state.udp_port
    ids = {c["id"] for c in out["checks"]}
    assert {"port_udp_data", "port_udp_beacon", "lan_ip", "loopback"} <= ids
    assert out["likely_cause"] in ("device_offline", "port_conflict", "no_network",
                                   "none", "firewall", "client_isolation",
                                   "different_network")


def test_support_report_writes_a_zip_into_exports(app_env):
    state = app_env.state.app_state
    state.log_buffer.info("provision", "joining HomeNet password=hunter2")
    state.log_buffer.info("recorder", "saved take_9.csv")
    out = asyncio.run(_route(app_env, "/api/support-report", "POST").endpoint())
    assert out["ok"] is True
    from openmuscle import paths
    assert Path(out["path"]).parent == paths.exports_dir()
    with zipfile.ZipFile(out["path"]) as z:
        blob = "\n".join(z.read(n).decode("utf-8") for n in z.namelist())
    assert "HomeNet" not in blob and "hunter2" not in blob and "take_9.csv" not in blob
    assert "versions.json" in zipfile.ZipFile(out["path"]).namelist()


def test_snapshot_carries_the_network_view(app_env):
    snap = app_env.state.app_state._snapshot()
    assert "network" in snap and "pc_host" in snap["network"]
    assert snap["network"]["listener_ok"]["data"] in (True, False, None)

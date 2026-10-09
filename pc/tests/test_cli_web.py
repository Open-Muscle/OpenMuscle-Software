"""`openmuscle web` defaults for non-engineers.

serve() is stubbed so no server starts; we assert on what the command decides
(bind address, TLS, browser URL, data folder) and on what it prints.
"""

import os
import socket

import pytest
from click.testing import CliRunner

from openmuscle import cli, paths
from openmuscle.protocol.schema import OpenMusclePacket
from openmuscle.web.state import AppState


@pytest.fixture
def run_web(tmp_path, monkeypatch):
    """Invoke `openmuscle web <args>` on a clean HOME; return (result, serve_kwargs, home)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    monkeypatch.delenv("OPENMUSCLE_SSL_CERTFILE", raising=False)
    monkeypatch.delenv("OPENMUSCLE_SSL_KEYFILE", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(home) + p[1:] if p.startswith("~") else p)
    paths.set_data_root(None)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    # Keep the test independent of whatever may hold port 8000 on this machine.
    monkeypatch.setattr(cli, "_port_free", lambda host, port: True)
    captured = {}

    import openmuscle.web.app as webapp
    monkeypatch.setattr(webapp, "serve", lambda **kw: captured.update(kw))

    def _run(*args):
        captured.clear()
        result = CliRunner().invoke(cli.main, ["web", *args])
        assert result.exit_code == 0, result.output
        return result, dict(captured), home

    return _run


def _make_certs(home):
    d = home / ".openmuscle"
    d.mkdir(exist_ok=True)
    (d / "vr-cert.pem").write_text("x")
    (d / "vr-key.pem").write_text("x")


def test_default_is_localhost_http_and_opens_browser(run_web):
    result, kw, home = run_web()
    assert kw["host"] == "127.0.0.1"
    assert kw["ssl_certfile"] is None and kw["ssl_keyfile"] is None
    assert kw["open_browser_url"] == "http://localhost:8000"
    assert kw["verbose"] is False
    out = result.output
    assert "http://localhost:8000" in out
    assert str(home / "OpenMuscle") in out
    assert str(home / "OpenMuscle" / "captures") in out
    assert "Leave this window open" in out
    assert "firewall" in out and "Allow (Private networks)" in out
    assert "Waiting for bands" in out


def test_default_output_has_no_vr_or_tls_talk(run_web):
    result, _kw, _home = run_web()
    low = result.output.lower()
    for word in ("tls", "webxr", "adb", "quest", "mkcert", "certificate", "https"):
        assert word not in low, word


def test_default_creates_data_folders_not_cwd_files(run_web):
    _result, _kw, home = run_web()
    for name in ("captures", "sessions", "models", "exports", "logs"):
        assert (home / "OpenMuscle" / name).is_dir()
    assert os.listdir(os.getcwd()) == []


def test_existing_certs_do_not_turn_on_tls_without_vr(run_web, tmp_path):
    _make_certs(tmp_path / "home")
    result, kw, _home = run_web()
    assert kw["ssl_certfile"] is None
    assert kw["open_browser_url"].startswith("http://")
    assert "tls" not in result.output.lower()


def test_lan_binds_all_interfaces(run_web):
    result, kw, _home = run_web("--lan")
    assert kw["host"] == "0.0.0.0"
    assert kw["ssl_certfile"] is None
    assert "Other devices on your network" in result.output


def test_explicit_host_wins(run_web):
    _result, kw, _home = run_web("--lan", "--host", "10.1.2.3")
    assert kw["host"] == "10.1.2.3"


def test_vr_with_certs_serves_https_on_lan(run_web, tmp_path):
    _make_certs(tmp_path / "home")
    result, kw, _home = run_web("--vr")
    assert kw["host"] == "0.0.0.0"
    assert kw["ssl_certfile"] and kw["ssl_keyfile"]
    assert kw["open_browser_url"] == "https://localhost:8000"
    assert "WebXR URL for the Quest" in result.output


def test_vr_without_certs_prints_setup_help(run_web):
    result, kw, _home = run_web("--vr")
    assert kw["host"] == "0.0.0.0"
    assert kw["ssl_certfile"] is None
    assert "mkcert" in result.output and "vr-setup.md" in result.output


def test_explicit_ssl_flags_force_https(run_web, tmp_path):
    cert = tmp_path / "c.pem"
    key = tmp_path / "k.pem"
    cert.write_text("x")
    key.write_text("x")
    _result, kw, _home = run_web("--ssl-certfile", str(cert), "--ssl-keyfile", str(key))
    assert kw["ssl_certfile"] == str(cert)
    assert kw["open_browser_url"] == "https://localhost:8000"
    assert kw["host"] == "0.0.0.0"          # headset workflow keeps working


def test_debug_is_lan_plain_http_even_with_certs(run_web, tmp_path):
    _make_certs(tmp_path / "home")
    _result, kw, _home = run_web("--vr", "--debug")
    assert kw["host"] == "0.0.0.0"
    assert kw["ssl_certfile"] is None
    assert kw["debug"] is True


def test_no_browser_and_verbose(run_web):
    result, kw, _home = run_web("--no-browser", "--verbose")
    assert kw["open_browser_url"] is None
    assert kw["verbose"] is True
    assert "opening it for you" not in result.output


def test_data_dir_flag_sets_root(run_web, tmp_path):
    mine = tmp_path / "mine"
    result, _kw, _home = run_web("--data-dir", str(mine))
    assert (mine / "captures").is_dir() and (mine / "models").is_dir()
    assert str(mine) in result.output


def test_env_home_sets_root(run_web, tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "envroot"))
    result, _kw, _home = run_web()
    assert (tmp_path / "envroot" / "captures").is_dir()
    assert str(tmp_path / "envroot") in result.output


def test_captures_dir_flag_still_works(run_web, tmp_path):
    caps = tmp_path / "legacy_caps"
    result, kw, _home = run_web("--captures-dir", str(caps))
    assert kw["captures_dir"] == str(caps)
    assert str(caps) in result.output


def test_legacy_cwd_data_prints_one_hint_line(run_web):
    merged = os.path.join(os.getcwd(), "data", "raw", "merged")
    os.makedirs(merged)
    with open(os.path.join(merged, "old.csv"), "w") as f:
        f.write("x")
    result, _kw, _home = run_web()
    hints = [ln for ln in result.output.splitlines() if "found older data" in ln]
    assert len(hints) == 1


def test_serve_defaults_are_quiet(monkeypatch):
    import uvicorn
    import openmuscle.web.app as webapp
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    webapp.serve(port=8123, udp_port=53970)
    assert calls[-1]["log_level"] == "warning"
    assert calls[-1]["access_log"] is False
    assert calls[-1]["host"] == "127.0.0.1"
    webapp.serve(port=8123, udp_port=53971, verbose=True)
    assert calls[-1]["log_level"] == "info" and calls[-1]["access_log"] is True


# ----- port fallback -----

def test_pick_port_falls_back_when_default_busy(capsys):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    busy = s.getsockname()[1]
    try:
        port = cli._pick_port("127.0.0.1", busy, explicit=False)
        assert port != busy and busy < port <= busy + 10
        assert "already in use" in capsys.readouterr().out
    finally:
        s.close()


def test_pick_port_explicit_busy_is_a_clean_error():
    import click
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    busy = s.getsockname()[1]
    try:
        with pytest.raises(click.ClickException) as ei:
            cli._pick_port("127.0.0.1", busy, explicit=True)
        assert "already in use" in ei.value.format_message()
    finally:
        s.close()


# ----- found / lost band lines -----

def _flexgrid_packet(device_id="fg-1", t=None):
    import time
    return OpenMusclePacket(
        version="1.0", device_type="flexgrid", device_id=device_id,
        timestamp_ms=1, data={"matrix": [[0] * 4 for _ in range(15)]},
        receive_time=time.time() if t is None else t)


def test_first_packet_prints_found_once(tmp_path):
    events = []
    s = AppState(udp_port=53972, captures_dir=str(tmp_path), enable_discovery=False,
                 on_device_event=lambda kind, msg: events.append((kind, msg)))
    s._handle_packet(_flexgrid_packet())
    s._handle_packet(_flexgrid_packet())
    assert [k for k, _ in events] == ["found"]
    assert events[0][1].startswith("Found FlexGrid band")


def test_silent_band_prints_lost_then_found_again(tmp_path):
    events = []
    s = AppState(udp_port=53973, captures_dir=str(tmp_path), enable_discovery=False,
                 on_device_event=lambda kind, msg: events.append(kind))
    s._handle_packet(_flexgrid_packet(t=1000.0))
    s._check_lost(1000.0 + AppState.DEVICE_LOST_AFTER_S + 1)
    s._check_lost(1000.0 + AppState.DEVICE_LOST_AFTER_S + 5)    # no second "lost"
    s._handle_packet(_flexgrid_packet())
    assert events == ["found", "lost", "found"]


def test_discovery_beacon_prints_found_with_ip_and_dedupes(tmp_path):
    events = []
    s = AppState(udp_port=53974, captures_dir=str(tmp_path),
                 on_device_event=lambda kind, msg: events.append(msg))
    s.discovery.auto_subscribe = False
    s.discovery.cache_path = str(tmp_path / "cache.json")
    s.discovery.on_announce({"id": "fg-9", "dev": "flexgrid"}, "192.168.1.23")
    s._handle_packet(_flexgrid_packet("fg-9"))           # first data frame: no repeat
    assert events == ["Found FlexGrid band at 192.168.1.23"]

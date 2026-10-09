"""openmuscle doctor: every check with fakes, the cause logic, and the reports."""

import json
import os
import socket
import time
import zipfile

import pytest
from click.testing import CliRunner

import openmuscle.discovery.manager as manager_module
from openmuscle import cli, doctor, netutil, paths
from openmuscle import firewall as fw
from openmuscle.discovery.manager import DiscoveryManager
from openmuscle.receiver.udp_listener import UDPListener
from tests.test_firewall import NOISE, _rule

CONTRACT_KEYS = {"ok", "checks", "udp_port", "bound_host", "devices_seen",
                 "last_announce_s", "firewall"}
CHECK_KEYS = {"id", "title", "status", "detail", "fix"}


def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _free_tcp_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _hold_udp(port=0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    netutil.make_exclusive(s)
    s.bind(("0.0.0.0", port))
    return s, s.getsockname()[1]


@pytest.fixture
def calm(monkeypatch, tmp_path):
    """A quiet machine: Windows, on Wi-Fi 'HomeNet', firewall open, internet up.
    Returns a dict the tests can change (platform, firewall probe, ssid)."""
    env = {"platform": "windows", "ssid": "HomeNet",
           "fw": {"readable": True, "profile": "private", "state": "on",
                  "ports": {3140: True, 3141: True}, "blocked_by": [],
                  "other_profile_rule": False, "rules": 40},
           "cache": str(tmp_path / "no_cache.json")}
    monkeypatch.setattr(netutil, "platform_name", lambda: env["platform"])
    monkeypatch.setattr(netutil, "wifi_ssid", lambda: env["ssid"])
    monkeypatch.setattr(netutil, "default_lan_ip", lambda *a, **k: "10.0.0.5")
    monkeypatch.setattr(manager_module, "default_lan_ip", lambda *a, **k: "10.0.0.5")
    monkeypatch.setattr(manager_module, "default_cache_path", lambda: env["cache"])
    monkeypatch.setattr(doctor, "_firewall_probe", lambda plat: env["fw"])
    monkeypatch.setattr(doctor, "_internet_probe", lambda: {"cdn.example.net": True})
    return env


def _run(env, **kw):
    kw.setdefault("discover", False)
    kw.setdefault("udp_port", _free_udp_port())
    kw.setdefault("announce_port", _free_udp_port())
    kw.setdefault("http_port", _free_tcp_port())
    kw.setdefault("cache_path", env["cache"])
    return doctor.run_doctor(**kw)


def _by_id(result):
    return {c["id"]: c for c in result["checks"]}


# ---- the contract ----------------------------------------------------------------

def test_result_has_exactly_the_documented_shape(calm):
    r = _run(calm)
    assert CONTRACT_KEYS <= set(r)
    assert set(r) - CONTRACT_KEYS == {"likely_cause", "advice"}
    assert isinstance(r["ok"], bool)
    assert r["checks"] and all(set(c) == CHECK_KEYS for c in r["checks"])
    assert all(c["status"] in ("pass", "warn", "fail") for c in r["checks"])
    assert set(r["firewall"]) == {"platform", "likely_blocked", "how_to_fix"}
    assert isinstance(r["firewall"]["likely_blocked"], bool)
    assert r["bound_host"] == "0.0.0.0"
    assert r["last_announce_s"] is None
    assert r["devices_seen"] == []
    assert r["likely_cause"] in doctor.CAUSES
    assert set(r["advice"]) == {"sentence", "action"}
    json.dumps(r)                                      # serialisable as is


def test_healthy_machine_passes_every_core_check(calm):
    r = _run(calm)
    c = _by_id(r)
    for cid in ("python", "packages", "static_files", "data_folder", "port_http",
                "port_udp_data", "port_udp_beacon", "lan_ip", "wifi", "firewall",
                "internet", "loopback"):
        assert c[cid]["status"] == "pass", (cid, c[cid])
    assert r["ok"] is True
    # no band powered on: a warning, never a failure
    assert c["bands"]["status"] == "warn"
    assert r["likely_cause"] == "device_offline"


# ---- ports ---------------------------------------------------------------------

def test_prebound_data_port_names_the_failure_and_the_fix(calm):
    s, port = _hold_udp()
    try:
        r = _run(calm, udp_port=port)
    finally:
        s.close()
    c = _by_id(r)["port_udp_data"]
    assert c["status"] == "fail"
    assert str(port) in c["detail"] and "already in use" in c["detail"]
    assert "--udp-port" in c["fix"] and "another OpenMuscle window" in c["fix"]
    assert r["ok"] is False
    assert r["likely_cause"] == "port_conflict"


def test_prebound_beacon_port_fails_too(calm):
    s, port = _hold_udp()
    try:
        r = _run(calm, announce_port=port)
    finally:
        s.close()
    c = _by_id(r)["port_udp_beacon"]
    assert c["status"] == "fail" and "--announce-port" in c["fix"]
    assert r["ok"] is False


def test_busy_http_port_fails_when_asked_for_and_warns_when_default(calm):
    hold = socket.socket()
    netutil.make_exclusive(hold)
    hold.bind(("127.0.0.1", 0))
    hold.listen(1)
    port = hold.getsockname()[1]
    try:
        explicit = _by_id(_run(calm, http_port=port, http_explicit=True))["port_http"]
        default = _by_id(_run(calm, http_port=port, http_explicit=False))["port_http"]
    finally:
        hold.close()
    assert explicit["status"] == "fail" and "--port" in explicit["fix"]
    assert default["status"] == "warn" and "next free port" in default["detail"]


def test_a_running_openmuscle_on_the_ports_is_not_a_failure(calm, monkeypatch):
    monkeypatch.setattr(netutil, "find_running_instance",
                        lambda *a, **k: {"app": "openmuscle"})
    s, port = _hold_udp()
    try:
        r = _run(calm, udp_port=port)
    finally:
        s.close()
    c = _by_id(r)["port_udp_data"]
    assert c["status"] == "warn" and "already running" in c["detail"]
    assert r["ok"] is True


# ---- the other checks ------------------------------------------------------------

def test_data_folder_not_writable_fails(calm, monkeypatch):
    def deny(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(doctor.tempfile, "mkstemp", deny)
    c = doctor.check_data_folder()
    assert c["status"] == "fail" and "--data-dir" in c["fix"]


def test_missing_static_files_fail(calm, monkeypatch):
    monkeypatch.setattr(doctor.os.path, "isfile", lambda p: False)
    c = doctor.check_static()
    assert c["status"] == "fail" and "index.html" in c["detail"]
    assert "reinstall" in c["fix"].lower()


def test_old_python_fails(monkeypatch):
    class V(tuple):
        pass

    monkeypatch.setattr(doctor.sys, "version_info", V((3, 8, 10, "final", 0)))
    c = doctor.check_python()
    assert c["status"] == "fail" and "3.10" in c["detail"]


def test_missing_package_fails(monkeypatch):
    real = doctor.importlib.util.find_spec
    monkeypatch.setattr(doctor.importlib.util, "find_spec",
                        lambda n: None if n == "sklearn" else real(n))
    c = doctor.check_packages()
    assert c["status"] == "fail" and "sklearn" in c["detail"]


def test_no_network_address_fails(calm, monkeypatch):
    monkeypatch.setattr(netutil, "default_lan_ip", lambda *a, **k: "127.0.0.1")
    monkeypatch.setattr(manager_module, "default_lan_ip", lambda *a, **k: "127.0.0.1")
    r = _run(calm)
    assert _by_id(r)["lan_ip"]["status"] == "fail"
    assert r["likely_cause"] == "no_network"
    assert r["ok"] is False


def test_wifi_warnings(calm):
    calm["ssid"] = None
    assert _by_id(_run(calm))["wifi"]["status"] == "warn"
    calm["ssid"] = "OM-flexgrid-d7af0b"
    c = _by_id(_run(calm))["wifi"]
    assert c["status"] == "warn" and "setup network" in c["detail"]
    assert "forget" in c["fix"].lower()


def test_wifi_name_is_shown_on_screen_but_hidden_when_redacted(calm):
    assert "HomeNet" in _by_id(_run(calm))["wifi"]["detail"]
    assert "HomeNet" not in json.dumps(_run(calm, redact=True))


def test_internet_unreachable_is_only_a_warning(calm, monkeypatch):
    monkeypatch.setattr(doctor, "_internet_probe", lambda: {"cdn.example.net": False})
    c = _by_id(_run(calm))["internet"]
    assert c["status"] == "warn" and "3D hand" in c["detail"]


def test_loopback_self_test_failure(calm, monkeypatch):
    class Dead(UDPListener):
        def start(self):
            self.bound = False
            self.bind_error = "UDP port 0 could not be opened."

    monkeypatch.setattr("openmuscle.receiver.udp_listener.UDPListener", Dead)
    c = doctor.check_loopback()
    assert c["status"] == "fail"


def test_a_check_that_crashes_does_not_break_the_run(calm, monkeypatch):
    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(doctor, "check_loopback", boom)
    r = _run(calm)
    assert _by_id(r)["loopback"]["status"] == "warn"
    assert "kaboom" in _by_id(r)["loopback"]["detail"]


# ---- firewall -> likely_cause ---------------------------------------------------

def _fw(rules_text, profile="private"):
    return dict(fw.evaluate(fw.parse_netsh_rules(rules_text), profile),
                readable=True, rules=10)


def test_no_inbound_rule_is_a_firewall_problem(calm):
    calm["fw"] = _fw(NOISE)
    r = _run(calm)
    c = _by_id(r)["firewall"]
    assert c["status"] == "fail"
    assert "No Windows firewall rule" in c["detail"]
    assert "openmuscle firewall" in c["fix"]
    assert r["likely_cause"] == "firewall"
    assert r["firewall"]["likely_blocked"] is True
    assert "openmuscle firewall" in r["firewall"]["how_to_fix"]
    assert r["ok"] is False


def test_rule_for_3141_only_is_a_firewall_problem(calm):
    calm["fw"] = _fw(NOISE + _rule("OpenMuscle data", ports="3141"))
    r = _run(calm)
    c = _by_id(r)["firewall"]
    assert c["status"] == "fail" and "3140" in c["detail"]
    assert r["likely_cause"] == "firewall"


def test_all_open_but_no_beacons_is_not_the_firewall(calm):
    calm["fw"] = _fw(NOISE + _rule("OpenMuscle", ports="3140,3141"))
    r = _run(calm)
    assert _by_id(r)["firewall"]["status"] == "pass"
    assert r["likely_cause"] == "device_offline"
    assert r["firewall"]["likely_blocked"] is False


def test_public_network_with_a_private_only_rule(calm):
    calm["fw"] = _fw(_rule("OpenMuscle", ports="3140,3141", profiles="Private"),
                     profile="public")
    r = _run(calm)
    c = _by_id(r)["firewall"]
    assert c["status"] == "fail" and "Public" in c["detail"]
    assert "Public" in c["fix"] and "Private" in c["fix"]


def test_unreadable_firewall_is_a_warning_not_a_guess(calm):
    calm["fw"] = {"readable": False, "ports": {}, "profile": None}
    r = _run(calm)
    assert _by_id(r)["firewall"]["status"] == "warn"
    assert r["firewall"]["likely_blocked"] is False


def test_mac_and_linux_firewall_advice(calm):
    calm["platform"] = "darwin"
    calm["fw"] = {"mac_enabled": True}
    r = _run(calm)
    assert _by_id(r)["firewall"]["status"] == "warn"
    assert "System Settings" in r["firewall"]["how_to_fix"]
    calm["platform"] = "linux"
    calm["fw"] = {}
    r = _run(calm)
    assert "firewall" not in _by_id(r)
    assert "ufw allow 3140:3141/udp" in r["firewall"]["how_to_fix"]
    assert r["firewall"]["platform"] == "linux"


# ---- infer_cause: the decision table -------------------------------------------

def _facts(**kw):
    base = {"lan_ip_ok": True, "listener_ok": None, "firewall_blocked": False,
            "beacon_recent": False, "data_flowing": False, "ssid": "HomeNet",
            "pc_host": "10.0.0.5", "devices": []}
    base.update(kw)
    return base


def _dev(ip="10.0.0.23", age=2.0, tcp=True, source="beacon", subscribed=False,
         heard=True, did="flexgrid-d7af0b"):
    return {"device_id": did, "ip": ip, "age_s": age, "tcp_ok": tcp,
            "source": source, "subscribed": subscribed, "heard": heard}


def test_cause_firewall_when_blocked_and_nothing_heard():
    assert doctor.infer_cause(_facts(firewall_blocked=True))[0] == "firewall"


def test_cause_firewall_when_band_answers_tcp_but_never_beacons():
    d = _dev(age=900.0, tcp=True, source="cache", heard=False)
    assert doctor.infer_cause(_facts(devices=[d]))[0] == "firewall"


def test_cause_firewall_when_connected_but_no_data_arrives():
    d = _dev(subscribed=True)
    assert doctor.infer_cause(_facts(devices=[d]))[0] == "firewall"
    assert doctor.infer_cause(_facts(devices=[d], data_flowing=True))[0] == "none"


def test_cause_different_network():
    d = _dev(ip="192.168.4.9", age=300.0, tcp=False, source="cache", heard=False)
    cause, sentence, action = doctor.infer_cause(_facts(devices=[d]))
    assert cause == "different_network"
    assert "192.168.4.9" in sentence and "10.0.0.5" in sentence
    assert "same Wi-Fi" in action


def test_cause_client_isolation_when_it_announces_but_cannot_be_reached():
    d = _dev(ip="10.0.0.23", age=1.0, tcp=False)
    cause, sentence, action = doctor.infer_cause(_facts(devices=[d]))
    assert cause == "client_isolation"
    assert "isolation" in action.lower() or "guest" in action.lower()


def test_cause_client_isolation_on_a_guest_network_when_nothing_answers():
    assert doctor.infer_cause(_facts(ssid="Cafe Guest WiFi"))[0] == "client_isolation"
    assert doctor.infer_cause(_facts(ssid="HomeNet"))[0] == "device_offline"


def test_cause_device_offline_known_band_not_answering():
    d = _dev(ip="10.0.0.23", age=3000.0, tcp=False, source="cache", heard=False)
    cause, sentence, _action = doctor.infer_cause(_facts(devices=[d]))
    assert cause == "device_offline" and "10.0.0.23" in sentence


def test_cause_device_offline_ignores_remembered_simulator_on_loopback():
    # A simulator cached from an earlier run lives at 127.0.0.1; it is not a
    # band, so the sentence must not point the user at it.
    d = _dev(ip="127.0.0.1", age=None, heard=False, tcp=False, source="cache",
             did="flexgrid-sim-left")
    cause, sentence, _action = doctor.infer_cause(_facts(devices=[d]))
    assert cause == "device_offline"
    assert "127.0.0.1" not in sentence
    assert "No band has answered" in sentence


def test_cause_device_offline_when_nothing_has_ever_been_seen():
    cause, sentence, action = doctor.infer_cause(_facts())
    assert cause == "device_offline"
    assert "powered on" in action


def test_cause_none_when_a_band_is_here():
    assert doctor.infer_cause(_facts(devices=[_dev()]))[0] == "none"


def test_a_band_found_by_tcp_probe_only_is_not_called_isolated():
    d = _dev(age=3.0, tcp=False, source="probe")       # was reachable moments ago
    assert doctor.infer_cause(_facts(devices=[d]))[0] == "device_offline"


def test_simulators_on_this_computer_never_blame_the_network():
    d = _dev(ip="127.0.0.1", tcp=True, subscribed=True)
    assert doctor.infer_cause(_facts(devices=[d]))[0] == "none"
    gone = _dev(ip="127.0.0.1", tcp=False, age=3000, source="cache", heard=False)
    assert doctor.infer_cause(_facts(devices=[gone]))[0] == "device_offline"


def test_no_network_and_port_conflict_come_first():
    assert doctor.infer_cause(_facts(lan_ip_ok=False, firewall_blocked=True))[0] == \
        "no_network"
    assert doctor.infer_cause(_facts(listener_ok=False, firewall_blocked=True))[0] == \
        "port_conflict"


def test_every_cause_has_a_sentence_and_an_action():
    cases = [_facts(firewall_blocked=True), _facts(lan_ip_ok=False),
             _facts(listener_ok=False), _facts(),
             _facts(devices=[_dev(tcp=False)]),
             _facts(devices=[_dev(ip="192.168.4.9", age=99, tcp=False,
                                  source="cache", heard=False)])]
    for f in cases:
        cause, sentence, action = doctor.infer_cause(f)
        assert sentence and action, cause


# ---- running server (live objects) ------------------------------------------------

def test_in_server_mode_judges_ports_by_whether_they_bound(calm, tmp_path):
    s, port = _hold_udp()
    bad = UDPListener(port=port, quiet=True)
    bad.start()
    disc = DiscoveryManager(pc_host="10.0.0.5", announce_port=_free_udp_port(),
                            cache_path="", auto_subscribe=False, discover_targets=[])
    try:
        r = doctor.run_doctor(http_port=8000, udp_port=port, discovery=disc,
                              data_listener=bad, discover=False,
                              cache_path=calm["cache"])
    finally:
        s.close()
    c = _by_id(r)
    assert c["port_http"]["status"] == "pass"
    assert c["port_udp_data"]["status"] == "fail"
    assert "already in use" in c["port_udp_data"]["detail"]
    assert r["likely_cause"] == "port_conflict"


def test_in_server_mode_reports_devices_and_beacon_age(calm, tmp_path):
    good = UDPListener(port=0, bind_ip="127.0.0.1", quiet=True)
    good.start()
    disc = DiscoveryManager(pc_host="10.0.0.5", cache_path="", auto_subscribe=False,
                            discover_targets=[])
    try:
        disc.on_announce({"v": "1.0", "type": "announce", "id": "flexgrid-d7af0b",
                          "dev": "flexgrid", "services": {"cmd": _free_tcp_port()}},
                         "10.0.0.23")
        r = doctor.run_doctor(http_port=8000, udp_port=good.bound_port,
                              discovery=disc, data_listener=good, discover=False,
                              cache_path=calm["cache"])
    finally:
        good.stop()
    assert r["bound_host"] == "127.0.0.1"
    assert [d["device_id"] for d in r["devices_seen"]] == ["flexgrid-d7af0b"]
    assert r["devices_seen"][0]["ip"] == "10.0.0.23"
    assert r["last_announce_s"] is not None and r["last_announce_s"] < 5
    assert _by_id(r)["firewall"]["status"] == "pass"


def test_remembered_bands_feed_the_diagnosis(calm, tmp_path):
    cache = tmp_path / "devices.json"
    cache.write_text(json.dumps([{"device_id": "flexgrid-old", "ip": "192.168.4.9",
                                  "cmd_port": _free_tcp_port(),
                                  "last_contact": time.time() - 4000}]))
    r = _run(calm, cache_path=str(cache))
    assert r["likely_cause"] == "different_network"
    assert r["devices_seen"] == []                       # remembered, not heard now


def test_standalone_run_finds_a_source_by_probe_without_a_beacon(calm, monkeypatch):
    from tests.test_discovery_health import FakeDiscoverSource
    src = FakeDiscoverSource("flexgrid-probe9")
    monkeypatch.setattr(DiscoveryManager, "broadcast_targets",
                        lambda self: ["127.0.0.1"])
    try:
        r = _run(calm, announce_port=src.port, discover=True)
    finally:
        src.stop()
    assert [d["device_id"] for d in r["devices_seen"]] == ["flexgrid-probe9"]
    assert r["last_announce_s"] is not None


# ---- speed ------------------------------------------------------------------------

def test_whole_run_stays_well_under_ten_seconds(calm):
    t0 = time.time()
    _run(calm, discover=True)
    assert time.time() - t0 < 9.0


def test_a_hung_check_cannot_hold_the_run_hostage(calm, monkeypatch):
    monkeypatch.setattr(doctor, "OVERALL_BUDGET_S", 1.0)

    def slow(plat):
        time.sleep(5)
        return calm["fw"]

    monkeypatch.setattr(doctor, "_firewall_probe", slow)
    t0 = time.time()
    r = _run(calm)
    assert time.time() - t0 < 4.0
    assert _by_id(r)["firewall"]["status"] == "warn"      # "could not read"


# ---- text, report, zip ---------------------------------------------------------------

def test_text_lines_are_plain_pass_fail(calm):
    s, port = _hold_udp()
    try:
        r = _run(calm, udp_port=port)
    finally:
        s.close()
    text = doctor.format_text(r)
    assert text.startswith("OpenMuscle doctor")
    assert "  PASS  Python version" in text
    assert "  FAIL  Sensor data port" in text
    assert "Fix: " in text
    assert "problem" in text.splitlines()[-1]


def test_report_has_versions_and_results_but_no_wifi_name(calm, tmp_path):
    r = _run(calm, redact=True)
    out = tmp_path / "rep.txt"
    doctor.write_report_file(r, str(out), ssid="HomeNet")
    text = out.read_text(encoding="utf-8")
    assert "openmuscle:" in text and "python:" in text and "platform:" in text
    assert "PASS  Python version" in text
    assert "HomeNet" not in text
    assert "no Wi-Fi names, passwords or recordings" in text


def test_scrub_removes_secrets_names_and_paths(tmp_path):
    raw = ("joined HomeNet with password=hunter2 ssid: Cafe ; wrote take_01.csv and "
           "model.pkl under " + str(paths.data_root() / "captures"))
    out = doctor.scrub(raw, ssid="HomeNet")
    assert "HomeNet" not in out and "hunter2" not in out and "Cafe" not in out
    assert "take_01.csv" not in out and "model.pkl" not in out
    assert str(paths.data_root()) not in out
    assert doctor.scrub('{"ssid": "X", "password": "Y"}').count("<hidden>") == 2


def test_support_zip_has_what_support_needs_and_nothing_private(calm, tmp_path):
    r = _run(calm, redact=True)
    logs = [
        {"id": 1, "t": time.time(), "level": "INFO", "source": "devices",
         "message": "Found FlexGrid band at 10.0.0.23"},
        {"id": 2, "t": time.time(), "level": "INFO", "source": "provision",
         "message": "joining HomeNet password=hunter2"},
        {"id": 3, "t": time.time(), "level": "INFO", "source": "recorder",
         "message": "saved take_17.csv (240 rows)"},
    ]
    path = doctor.build_support_zip(r, logs, ssid="HomeNet", out_dir=tmp_path)
    assert os.path.dirname(path) == str(tmp_path)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        blob = "\n".join(z.read(n).decode("utf-8") for n in names)
        assert {"report.txt", "doctor.json", "versions.json",
                "recent-logs.txt"} <= names
        assert "Found FlexGrid band at 10.0.0.23" in z.read("recent-logs.txt").decode()
    assert "HomeNet" not in blob and "hunter2" not in blob and "take_17.csv" not in blob
    assert not any(n.endswith(".csv") for n in names)


# ---- the command ---------------------------------------------------------------------

def test_doctor_command_exit_zero_when_healthy(calm):
    res = CliRunner().invoke(cli.main, [
        "doctor", "--no-discover", "--port", str(_free_tcp_port()),
        "--udp-port", str(_free_udp_port()),
        "--announce-port", str(_free_udp_port())])
    assert res.exit_code == 0, res.output
    assert "PASS  Python version" in res.output
    assert "Everything needed is working" in res.output


def test_doctor_command_exit_one_and_names_the_fix(calm):
    s, port = _hold_udp()
    try:
        res = CliRunner().invoke(cli.main, [
            "doctor", "--no-discover", "--udp-port", str(port),
            "--port", str(_free_tcp_port()),
            "--announce-port", str(_free_udp_port())])
    finally:
        s.close()
    assert res.exit_code == 1
    assert "FAIL  Sensor data port" in res.output
    assert "Fix:" in res.output and "openmuscle web --udp-port" in res.output


def test_doctor_command_report_flag_writes_a_file(calm, tmp_path):
    out = tmp_path / "support.txt"
    res = CliRunner().invoke(cli.main, [
        "doctor", "--no-discover", "--report", str(out),
        "--port", str(_free_tcp_port()), "--udp-port", str(_free_udp_port()),
        "--announce-port", str(_free_udp_port())])
    assert res.exit_code == 0, res.output
    text = out.read_text(encoding="utf-8")
    assert "OpenMuscle support report" in text and "HomeNet" not in text
    assert str(out) in res.output


def test_doctor_help_is_readable():
    res = CliRunner().invoke(cli.main, ["doctor", "--help"])
    assert res.exit_code == 0
    assert "what to fix" in res.output and "--report" in res.output


# ---- openmuscle firewall --------------------------------------------------------------

def test_firewall_command_prints_and_changes_nothing_by_default(monkeypatch):
    monkeypatch.setattr(netutil, "IS_WINDOWS", True)
    ran = []
    monkeypatch.setattr(fw, "run_elevated", lambda args, **k: ran.append(args) or True)
    res = CliRunner().invoke(cli.main, ["firewall"], input="n\n")
    assert res.exit_code == 0
    assert 'netsh advfirewall firewall add rule name="OpenMuscle UDP 3140-3141"' \
        in res.output
    assert "localport=3140,3141" in res.output
    assert "Nothing was changed" in res.output
    assert ran == []


def test_firewall_command_print_only(monkeypatch):
    monkeypatch.setattr(netutil, "IS_WINDOWS", True)
    monkeypatch.setattr(fw, "run_elevated",
                        lambda *a, **k: pytest.fail("must not elevate"))
    res = CliRunner().invoke(cli.main, ["firewall", "--print-only"])
    assert res.exit_code == 0 and "New-NetFirewallRule" in res.output


def test_firewall_command_adds_the_rule_after_a_yes(monkeypatch):
    monkeypatch.setattr(netutil, "IS_WINDOWS", True)
    ran = []
    monkeypatch.setattr(fw, "run_elevated", lambda args, **k: ran.append(args) or True)
    monkeypatch.setattr(fw, "inspect", lambda **k: {
        "readable": True, "ports": {3140: True, 3141: True}})
    res = CliRunner().invoke(cli.main, ["firewall"], input="y\n")
    assert res.exit_code == 0, res.output
    assert ran and "profile=private,domain" in ran[0]
    assert "Done" in res.output


def test_firewall_command_public_and_remove(monkeypatch):
    monkeypatch.setattr(netutil, "IS_WINDOWS", True)
    ran = []
    monkeypatch.setattr(fw, "run_elevated", lambda args, **k: ran.append(args) or True)
    monkeypatch.setattr(fw, "inspect", lambda **k: {"readable": True, "ports": {}})
    CliRunner().invoke(cli.main, ["firewall", "--public", "--yes"])
    assert "profile=any" in ran[0]
    res = CliRunner().invoke(cli.main, ["firewall", "--remove", "--yes"])
    assert "delete" in ran[1] and "removed" in res.output


def test_firewall_command_declined_admin_prompt_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(netutil, "IS_WINDOWS", True)
    monkeypatch.setattr(fw, "run_elevated", lambda *a, **k: False)
    res = CliRunner().invoke(cli.main, ["firewall", "--yes"])
    assert res.exit_code != 0 and "was not changed" in res.output


def test_firewall_command_on_other_systems_only_explains(monkeypatch):
    monkeypatch.setattr(netutil, "IS_WINDOWS", False)
    monkeypatch.setattr(fw, "run_elevated",
                        lambda *a, **k: pytest.fail("must not elevate"))
    res = CliRunner().invoke(cli.main, ["firewall"])
    assert res.exit_code == 0
    assert "ufw allow 3140:3141/udp" in res.output

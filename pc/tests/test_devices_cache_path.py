"""The remembered-bands file lives under the data root (devices.json), is copied
once from the older profile location, and is what the doctor and the storage
panel read."""
import json
import time

import pytest

from openmuscle import doctor, netutil, paths
from openmuscle.discovery import manager as manager_module
from openmuscle.discovery.manager import DiscoveryManager, default_cache_path
from openmuscle.web.state import AppState


def _entry(device_id="flexgrid-aaa", ip="10.0.0.5"):
    return {"device_id": device_id, "ip": ip, "cmd_port": 8001,
            "last_contact": time.time() - 60}


def _announce(device_id="flexgrid-aaa"):
    return {"v": "1.0", "type": "announce", "id": device_id, "role": "source",
            "dev": "flexgrid", "fw": "v4.0.0", "transports": ["wifi"],
            "caps": ["sensor"], "matrix": [15, 4],
            "services": {"sensor": 3141, "cmd": 8001}, "ts": 12345}


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """A fake older profile file location, empty until a test writes it."""
    old = tmp_path / "profile" / ".openmuscle" / "discovered_devices.json"
    monkeypatch.setattr(paths, "legacy_devices_cache_path", lambda: old)
    return old


def _write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


# ---- path resolution --------------------------------------------------------

def test_cache_path_follows_the_data_root(tmp_path):
    paths.set_data_root(tmp_path / "one")
    assert paths.devices_cache_path() == tmp_path / "one" / "devices.json"
    assert default_cache_path() == str(tmp_path / "one" / "devices.json")
    paths.set_data_root(tmp_path / "two")
    assert default_cache_path() == str(tmp_path / "two" / "devices.json")


def test_cache_path_follows_openmuscle_home(tmp_path, monkeypatch):
    paths.set_data_root(None)
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "env_root"))
    assert default_cache_path() == str(tmp_path / "env_root" / "devices.json")


def test_data_dir_wins_over_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "env_root"))
    paths.set_data_root(tmp_path / "flag_root")
    assert default_cache_path() == str(tmp_path / "flag_root" / "devices.json")


def test_manager_defaults_to_the_data_root_file(tmp_path):
    paths.set_data_root(tmp_path / "root")
    m = DiscoveryManager(pc_host="127.0.0.1", auto_subscribe=False)
    assert m.cache_path == str(tmp_path / "root" / "devices.json")
    m.on_announce(_announce(), "10.0.0.5")
    assert (tmp_path / "root" / "devices.json").is_file()
    m.stop()


def test_explicit_cache_path_override_still_works(tmp_path):
    paths.set_data_root(tmp_path / "root")
    mine = tmp_path / "elsewhere" / "mine.json"
    m = DiscoveryManager(pc_host="127.0.0.1", cache_path=str(mine),
                         auto_subscribe=False)
    assert m.cache_path == str(mine)
    m.on_announce(_announce(), "10.0.0.5")
    assert mine.is_file()
    assert not (tmp_path / "root" / "devices.json").exists()
    m.stop()


# ---- the one-time copy ------------------------------------------------------

def test_old_file_is_copied_once_when_new_is_missing(tmp_path, legacy):
    paths.set_data_root(tmp_path / "root")
    _write(legacy, [_entry("flexgrid-old")])
    new = paths.devices_cache_path()
    assert not new.exists()
    assert default_cache_path() == str(new)
    assert json.loads(new.read_text(encoding="utf-8"))[0]["device_id"] == "flexgrid-old"
    assert legacy.is_file()                              # the old file is left alone


def test_copy_never_overwrites_an_existing_new_file(tmp_path, legacy):
    paths.set_data_root(tmp_path / "root")
    _write(legacy, [_entry("flexgrid-old")])
    _write(paths.devices_cache_path(), [_entry("flexgrid-new")])
    default_cache_path()
    got = json.loads(paths.devices_cache_path().read_text(encoding="utf-8"))
    assert [e["device_id"] for e in got] == ["flexgrid-new"]


def test_copy_happens_only_once(tmp_path, legacy):
    paths.set_data_root(tmp_path / "root")
    _write(legacy, [_entry("flexgrid-old")])
    default_cache_path()
    # The new file is then emptied; a later change to the old one is not re-copied.
    _write(paths.devices_cache_path(), [])
    _write(legacy, [_entry("flexgrid-later")])
    default_cache_path()
    assert json.loads(paths.devices_cache_path().read_text(encoding="utf-8")) == []


def test_no_old_file_means_no_new_file(tmp_path, legacy):
    paths.set_data_root(tmp_path / "root")
    default_cache_path()
    assert not paths.devices_cache_path().exists()


def test_a_second_data_root_does_not_inherit_the_first_roots_bands(tmp_path, legacy):
    paths.set_data_root(tmp_path / "one")
    m = DiscoveryManager(pc_host="127.0.0.1", auto_subscribe=False)
    m.on_announce(_announce("flexgrid-sim"), "127.0.0.1")
    m.stop()
    paths.set_data_root(tmp_path / "two")
    m2 = DiscoveryManager(pc_host="127.0.0.1", auto_subscribe=False)
    assert m2._load_cache() == []
    m2.stop()


# ---- doctor -----------------------------------------------------------------

@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(netutil, "wifi_ssid", lambda: "HomeNet")
    monkeypatch.setattr(netutil, "default_lan_ip", lambda *a, **k: "10.0.0.5")
    monkeypatch.setattr(manager_module, "default_lan_ip", lambda *a, **k: "10.0.0.5")
    monkeypatch.setattr(doctor, "_firewall_probe", lambda plat: {})
    monkeypatch.setattr(doctor, "_internet_probe", lambda: {})


def _ctx():
    return doctor._make_ctx(8000, 3141, 3140, False, None, None, False, False, None)


def test_doctor_devices_come_from_the_data_root_file(tmp_path, quiet):
    paths.set_data_root(tmp_path / "root")
    _write(paths.devices_cache_path(), [_entry("flexgrid-here", "192.168.4.9")])
    got = {d["device_id"]: d for d in doctor._known_devices(_ctx())}
    assert set(got) == {"flexgrid-here"}
    assert got["flexgrid-here"]["source"] == "cache"
    assert got["flexgrid-here"]["heard"] is False


def test_doctor_does_not_see_bands_of_another_data_root(tmp_path, quiet):
    paths.set_data_root(tmp_path / "one")
    _write(paths.devices_cache_path(), [_entry("flexgrid-sim", "127.0.0.1")])
    paths.set_data_root(tmp_path / "two")
    assert doctor._known_devices(_ctx()) == []


def test_doctor_devices_include_the_one_time_copy(tmp_path, legacy, quiet):
    paths.set_data_root(tmp_path / "root")
    _write(legacy, [_entry("flexgrid-old", "192.168.4.7")])
    assert [d["device_id"] for d in doctor._known_devices(_ctx())] == ["flexgrid-old"]


def test_doctor_lists_the_file_in_its_checks_and_report(tmp_path, quiet):
    paths.set_data_root(tmp_path / "root")
    _write(paths.devices_cache_path(), [_entry("flexgrid-a"), _entry("flexgrid-b")])
    r = doctor.run_doctor(http_port=0, udp_port=0, announce_port=0, discover=False)
    chk = {c["id"]: c for c in r["checks"]}["devices_file"]
    assert chk["status"] == "pass"
    assert str(paths.devices_cache_path()) in chk["detail"]
    assert "2 remembered" in chk["detail"]
    text = doctor.report_text(r)
    assert "Remembered bands file" in text           # the report lists the file (its name is scrubbed)


def test_doctor_check_handles_a_missing_and_a_broken_file(tmp_path):
    paths.set_data_root(tmp_path / "root")
    ctx = {"cache_path": None, "discovery": None}
    assert "no band has been seen" in doctor.check_devices_file(ctx)["detail"]
    p = paths.devices_cache_path()
    p.parent.mkdir(parents=True)
    p.write_text("not json", encoding="utf-8")
    c = doctor.check_devices_file(ctx)
    assert c["status"] == "pass" and "could not be read" in c["detail"]


# ---- storage panel ----------------------------------------------------------

def test_storage_info_lists_the_devices_file(tmp_path):
    paths.set_data_root(tmp_path / "root")
    state = AppState(udp_port=53975, enable_discovery=False)
    assert state.storage_info()["devices"] == str(tmp_path / "root" / "devices.json")


def test_storage_info_follows_a_cache_path_override(tmp_path):
    paths.set_data_root(tmp_path / "root")
    state = AppState(udp_port=53976, enable_discovery=True)
    try:
        state.discovery.cache_path = str(tmp_path / "x" / "mine.json")
        assert state.storage_info()["devices"] == str(tmp_path / "x" / "mine.json")
    finally:
        state.discovery.stop()


def test_dashboard_panel_has_a_row_for_the_devices_file():
    from pathlib import Path
    import openmuscle.web as web
    html = (Path(web.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    assert 'data-storage-path="devices"' in html
    assert 'data-storage-copy="devices"' in html

"""`openmuscle setup-usb`: Wi-Fi setup over the USB cable, against the fake band
(tests/usb_fakes.py). No serial port is opened."""

import pytest
from click.testing import CliRunner

from openmuscle.cli import main
from openmuscle.provision import usb

from tests.usb_fakes import BAND_ID, PASSWORD, SSID, FakeBand, install_bench


@pytest.fixture
def bench(monkeypatch):
    # short waits: the defaults of these calls are the real 3 s ones
    real_list, real_info = usb.list_ports, usb.info
    monkeypatch.setattr(usb, "list_ports", lambda: real_list(timeout=0.2, tries=1))
    monkeypatch.setattr(usb, "info", lambda port: real_info(port, timeout=0.2))
    real_set, real_forget = usb.set_wifi, usb.forget_wifi
    monkeypatch.setattr(usb, "set_wifi", lambda *a, **k: real_set(*a, timeout=0.2, **k))
    monkeypatch.setattr(usb, "forget_wifi", lambda *a, **k: real_forget(*a, timeout=0.2, **k))
    return install_bench(monkeypatch)


def run(args, input=None):
    return CliRunner().invoke(main, ["setup-usb"] + args, input=input)


def test_picks_the_one_band_and_sends_the_details(bench):
    band = bench.add("COM5", FakeBand())
    r = run(["--ssid", SSID, "--password", PASSWORD])
    assert r.exit_code == 0, r.output
    assert BAND_ID in r.output and "restarting" in r.output
    assert PASSWORD not in r.output
    assert band.received[-1]["data"] == {"verb": "set_wifi", "ssid": SSID, "password": PASSWORD}


def test_prompts_for_the_password_hidden(bench):
    band = bench.add("COM5", FakeBand())
    r = run(["--ssid", SSID], input=PASSWORD + "\n")
    assert r.exit_code == 0, r.output
    assert PASSWORD not in r.output                  # not echoed, not repeated
    assert band.received[-1]["data"]["password"] == PASSWORD


def test_an_empty_password_means_an_open_network(bench):
    band = bench.add("COM5", FakeBand())
    r = run(["--ssid", "Cafe"], input="\n")
    assert r.exit_code == 0, r.output
    assert band.received[-1]["data"]["password"] == ""


def test_explicit_port_with_two_bands_plugged_in(bench):
    bench.add("COM5", FakeBand(band_id="flexgrid-000005"))
    two = bench.add("COM6", FakeBand(band_id="flexgrid-000006"))
    r = run(["--port", "COM6", "--ssid", SSID, "--password", PASSWORD])
    assert r.exit_code == 0, r.output
    assert "flexgrid-000006" in r.output
    assert two.received[-1]["data"]["verb"] == "set_wifi"


def test_several_bands_without_a_port_is_an_error(bench):
    bench.add("COM5", FakeBand(band_id="flexgrid-000005"))
    bench.add("COM6", FakeBand(band_id="flexgrid-000006"))
    r = run(["--ssid", SSID, "--password", PASSWORD])
    assert r.exit_code != 0
    assert "Unplug all but the one" in r.output


def test_no_band_plugged_in(bench):
    r = run(["--ssid", SSID, "--password", PASSWORD])
    assert r.exit_code != 0
    assert "data" in r.output and "charge" in r.output


def test_a_silent_band_says_replug(bench):
    bench.add("COM5", FakeBand(silent=True))
    r = run(["--ssid", SSID, "--password", PASSWORD])
    assert r.exit_code != 0
    assert "plug it back in (or press the BOOT button)" in r.output


def test_bad_password_length_is_refused_before_anything_is_sent(bench):
    band = bench.add("COM5", FakeBand())
    r = run(["--ssid", SSID, "--password", "short"])
    assert r.exit_code != 0
    assert "8 to 63" in r.output
    assert "short" not in r.output.replace("shortcut", "")
    assert [c["data"]["verb"] for c in band.received] == ["get_info"]


def test_the_band_can_refuse(bench):
    bench.add("COM5", FakeBand(errors={"set_wifi": ("save_failed", "flash error")}))
    r = run(["--ssid", SSID, "--password", PASSWORD])
    assert r.exit_code != 0
    assert "could not save" in r.output
    assert PASSWORD not in r.output


def test_ssid_is_required_unless_forgetting(bench):
    r = run([])
    assert r.exit_code != 0 and "--ssid" in r.output


def test_forget(bench):
    band = bench.add("COM5", FakeBand(state="provisioned", wifi_ssid="Home"))
    r = run(["--forget"])
    assert r.exit_code == 0, r.output
    assert band.received[-1]["data"] == {"verb": "forget_wifi"}
    assert "setup mode" in r.output


def test_forget_does_not_take_wifi_details(bench):
    r = run(["--forget", "--ssid", SSID])
    assert r.exit_code != 0 and "--forget" in r.output


def test_the_command_is_listed():
    r = CliRunner().invoke(main, ["--help"])
    assert "setup-usb" in r.output

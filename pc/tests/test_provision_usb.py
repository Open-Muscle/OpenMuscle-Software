"""Tests for openmuscle.provision.usb (Wi-Fi setup over the band's USB cable).

No serial port is ever opened: a FakeSerial (tests/usb_fakes.py) speaks the
band's side of the line protocol (one JSON object per line, log noise on the
same port, replies matched by id) and stands in for pyserial through the
module's two seams.
"""

import json
import logging
import time

import pytest

from openmuscle.provision import usb
from openmuscle.provision.client import ProvisionError

from tests.usb_fakes import (BAND_ID, PASSWORD, SSID, FakeBand, FakePort, FakeSerial,
                             install_bench)

FAST = 0.25            # command timeout used by the tests that wait for silence


@pytest.fixture
def bench(monkeypatch):
    return install_bench(monkeypatch)


# ---- find / list -----------------------------------------------------------------

def test_find_ports_keeps_only_the_band_vid_and_pid(bench):
    bench.add("COM7", FakeBand())
    bench.add("COM3", FakeBand(band_id="flexgrid-000001"))
    bench.ports.append(FakePort("COM9", vid=0x1A86, pid=0x7523))      # some other adapter
    bench.ports.append(FakePort("COM10", vid=usb.USB_VID, pid=0x1001))  # same maker, other product
    bench.ports.append(FakePort("COM11", vid=None, pid=None))
    assert [p.port for p in usb.find_ports()] == ["COM3", "COM7"]


def test_list_ports_reports_id_role_and_state(bench):
    bench.add("COM5", FakeBand(role="right", state="provisioned", wifi_ssid="Home"))
    ports = usb.list_ports(timeout=FAST, tries=1)
    assert len(ports) == 1
    p = ports[0]
    assert p.answered and p.port == "COM5"
    assert (p.band_id, p.role, p.state, p.wifi_ssid) == (BAND_ID, "right", "provisioned", "Home")
    d = p.to_dict()
    assert d["id"] == BAND_ID and d["answered"] is True


def test_list_ports_marks_a_silent_band_as_not_answering(bench):
    bench.add("COM5", FakeBand(silent=True))
    ports = usb.list_ports(timeout=FAST, tries=1)
    assert len(ports) == 1 and ports[0].answered is False and ports[0].band_id == ""


def test_list_ports_without_probe_opens_nothing(bench):
    band = bench.add("COM5", FakeBand())
    ports = usb.list_ports(probe=False)
    assert [p.port for p in ports] == ["COM5"] and band.opened == 0


def test_list_ports_survives_a_port_that_cannot_be_opened(bench):
    bench.add("COM5", FakeBand())
    bench.fail_open.add("COM5")
    ports = usb.list_ports(timeout=FAST, tries=1)
    assert len(ports) == 1 and ports[0].answered is False


def test_pick_port_rules():
    a = usb.UsbPort("A", answered=True, band_id="x")
    b = usb.UsbPort("B", answered=True, band_id="y")
    silent = usb.UsbPort("C")
    assert usb.pick_port([a, silent]) is a
    with pytest.raises(usb.UsbSetupError) as e:
        usb.pick_port([a, b])
    assert e.value.code == "many_ports"
    with pytest.raises(usb.UsbSetupError) as e:
        usb.pick_port([silent])
    assert e.value.code == "no_answer"
    with pytest.raises(usb.UsbSetupError) as e:
        usb.pick_port([])
    assert e.value.code == "no_port"


# ---- info ------------------------------------------------------------------------

def test_info_ignores_log_noise_and_non_json(bench):
    band = bench.add("COM5", FakeBand(noise=[
        "I (1234) wifi: scanning", "[boot] mounted fs", "  ", "{not json at all",
        '{"type":"log","msg":"a json log line"}', '[1,2,3]']))
    got = usb.info("COM5", timeout=FAST)
    assert got.band_id == BAND_ID and got.role == "left" and got.state == "unprovisioned"
    assert got.usb_console == 1 and got.fw == "1.2.3"
    assert band.received[0]["data"] == {"verb": "get_info"}
    assert band.closed == 1


def test_info_ignores_a_reply_with_the_wrong_id(bench):
    bench.add("COM5", FakeBand(wrong_id=True))
    assert usb.info("COM5", timeout=FAST).band_id == BAND_ID


def test_info_only_wrong_id_means_no_answer(bench):
    class OnlyWrong(FakeBand):
        def reply_for(self, cmd):
            return dict(super().reply_for(cmd), id="someone-else")
    bench.add("COM5", OnlyWrong())
    with pytest.raises(usb.UsbSetupError) as e:
        usb.info("COM5", timeout=0.1)
    assert e.value.code == "no_answer"


def test_info_retries_twice_then_succeeds(bench):
    band = bench.add("COM5", FakeBand(silent_first=2))
    assert usb.info("COM5", timeout=0.1).band_id == BAND_ID
    assert len(band.received) == 3
    assert len({c["id"] for c in band.received}) == 3          # a fresh id per try


def test_info_gives_up_after_three_tries(bench):
    band = bench.add("COM5", FakeBand(silent=True))
    with pytest.raises(usb.UsbSetupError) as e:
        usb.info("COM5", timeout=0.1)
    assert e.value.code == "no_answer"
    assert len(band.received) == 3
    assert band.closed == 1


def test_a_late_reply_to_the_first_try_is_still_accepted(bench):
    band = bench.add("COM5", FakeBand(late_first=True))
    got = usb.info("COM5", timeout=0.15)
    assert got.band_id == BAND_ID
    assert len(band.received) == 2


def test_no_answer_message_says_replug_or_boot(bench):
    bench.add("COM5", FakeBand(silent=True))
    with pytest.raises(usb.UsbSetupError) as e:
        usb.info("COM5", timeout=0.05)
    msg = e.value.message
    assert "Unplug the cable and plug it back in (or press the BOOT button), then try again." in msg


def test_a_port_that_cannot_be_opened_is_no_port(bench):
    with pytest.raises(usb.UsbSetupError) as e:
        usb.info("COM99", timeout=FAST)
    assert e.value.code == "no_port"


def test_read_and_write_failures_mean_no_answer(monkeypatch):
    band = FakeBand()
    monkeypatch.setattr(usb, "_open_serial", lambda port: FakeSerial(band, fail_read=True))
    with pytest.raises(usb.UsbSetupError) as e:
        usb.info("COM5", timeout=FAST)
    assert e.value.code == "no_answer"
    monkeypatch.setattr(usb, "_open_serial", lambda port: FakeSerial(band, fail_write=True))
    with pytest.raises(usb.UsbSetupError) as e:
        usb.info("COM5", timeout=FAST)
    assert e.value.code == "no_answer"


def test_a_reply_without_an_id_field_in_data_is_no_answer(bench):
    class NoId(FakeBand):
        def reply_for(self, cmd):
            r = super().reply_for(cmd)
            r["data"] = {"role": "left"}
            return r
    bench.add("COM5", NoId())
    with pytest.raises(usb.UsbSetupError) as e:
        usb.info("COM5", timeout=0.1)
    assert e.value.code == "no_answer"


# ---- set_wifi / forget_wifi / set_role --------------------------------------------

def test_set_wifi_sends_the_details_and_reports_the_band(bench):
    band = bench.add("COM5", FakeBand(noise=["I (99) boot: hello"]))
    t0 = time.time()
    res = usb.set_wifi("COM5", SSID, PASSWORD, timeout=FAST)
    assert res.band_id == BAND_ID and res.restart is True and res.completed_at >= t0
    verbs = [c["data"]["verb"] for c in band.received]
    assert verbs == ["get_info", "set_wifi"]
    sent = band.received[1]["data"]
    assert sent == {"verb": "set_wifi", "ssid": SSID, "password": PASSWORD}
    assert band.closed == 1


def test_set_wifi_open_network_sends_an_empty_password(bench):
    band = bench.add("COM5", FakeBand())
    usb.set_wifi("COM5", "Cafe", "", timeout=FAST)
    assert band.received[1]["data"]["password"] == ""


def test_set_wifi_validates_before_opening_the_port(bench):
    band = bench.add("COM5", FakeBand())
    with pytest.raises(ProvisionError) as e:
        usb.set_wifi("COM5", "", PASSWORD)
    assert e.value.code == "ssid_length_invalid"
    with pytest.raises(ProvisionError) as e:
        usb.set_wifi("COM5", SSID, "short")
    assert e.value.code == "password_length_invalid"
    with pytest.raises(ProvisionError):
        usb.set_wifi("COM5", "x" * 33, PASSWORD)
    with pytest.raises(ProvisionError):
        usb.set_wifi("COM5", SSID, "p" * 64)
    assert band.opened == 0 and band.received == []


def test_set_wifi_refuses_a_different_band_than_expected(bench):
    band = bench.add("COM5", FakeBand())
    with pytest.raises(usb.UsbSetupError) as e:
        usb.set_wifi("COM5", SSID, PASSWORD, expect_id="flexgrid-000001", timeout=FAST)
    assert e.value.code == "rejected" and e.value.band_code == "wrong_device"
    assert BAND_ID in e.value.message
    assert [c["data"]["verb"] for c in band.received] == ["get_info"]      # never sent


@pytest.mark.parametrize("code,needle", [
    ("bad_ssid", "Wi-Fi name"),
    ("bad_password", "password"),
    ("save_failed", "could not save"),
    ("bad_json", "garbled"),
    ("unknown_verb", "own Wi-Fi"),
])
def test_every_band_error_code_is_rejected_with_plain_words(bench, code, needle):
    bench.add("COM5", FakeBand(errors={"set_wifi": (code, "band says: " + code)}))
    with pytest.raises(usb.UsbSetupError) as e:
        usb.set_wifi("COM5", SSID, PASSWORD, timeout=FAST)
    err = e.value
    assert err.code == "rejected"
    assert err.band_code == code and err.band_message == "band says: " + code
    assert needle in err.message
    assert PASSWORD not in err.message + repr(err) + err.detail


def test_an_unlisted_band_error_uses_the_bands_own_message(bench):
    bench.add("COM5", FakeBand(errors={"set_wifi": ("flash_worn", "The flash is worn out.")}))
    with pytest.raises(usb.UsbSetupError) as e:
        usb.set_wifi("COM5", SSID, PASSWORD, timeout=FAST)
    assert e.value.band_code == "flash_worn" and e.value.message == "The flash is worn out."


def test_a_band_error_with_no_message_still_reads_as_a_sentence(bench):
    bench.add("COM5", FakeBand(errors={"set_wifi": ("flash_worn", "")}))
    with pytest.raises(usb.UsbSetupError) as e:
        usb.set_wifi("COM5", SSID, PASSWORD, timeout=FAST)
    assert e.value.message == usb._MESSAGES["rejected"]


def test_set_wifi_does_not_retry_when_the_band_is_silent_after_get_info(bench):
    band = bench.add("COM5", FakeBand(mute_verbs={"set_wifi"}))
    with pytest.raises(usb.UsbSetupError) as e:
        usb.set_wifi("COM5", SSID, PASSWORD, timeout=0.1)
    assert e.value.code == "no_answer"
    assert [c["data"]["verb"] for c in band.received].count("set_wifi") == 1


def test_forget_wifi(bench):
    band = bench.add("COM5", FakeBand(state="provisioned", wifi_ssid="Home"))
    res = usb.forget_wifi("COM5", timeout=FAST)
    assert res.band_id == BAND_ID and res.restart is True
    assert [c["data"]["verb"] for c in band.received] == ["get_info", "forget_wifi"]
    assert band.received[1]["data"] == {"verb": "forget_wifi"}


def test_forget_wifi_error_is_rejected(bench):
    bench.add("COM5", FakeBand(errors={"forget_wifi": ("save_failed", "flash")}))
    with pytest.raises(usb.UsbSetupError) as e:
        usb.forget_wifi("COM5", timeout=FAST)
    assert e.value.code == "rejected" and e.value.band_code == "save_failed"


def test_set_role(bench):
    band = bench.add("COM5", FakeBand())
    res = usb.set_role("COM5", "Right", timeout=FAST)
    assert res.role == "right" and res.restart is False
    assert band.received[-1]["data"] == {"verb": "set_role", "role": "right"}
    with pytest.raises(usb.UsbSetupError) as e:
        usb.set_role("COM5", "middle", timeout=FAST)
    assert e.value.code == "rejected"


def test_the_password_never_reaches_a_log_or_an_exception(bench, caplog):
    caplog.set_level(logging.DEBUG, logger="openmuscle")
    bench.add("COM5", FakeBand(noise=["echo " + PASSWORD],
                               errors={"set_wifi": ("bad_password", "echo " + PASSWORD)}))
    with pytest.raises(usb.UsbSetupError) as e:
        usb.set_wifi("COM5", SSID, PASSWORD, timeout=FAST)
    assert PASSWORD not in caplog.text
    assert PASSWORD not in str(e.value) + repr(e.value) + e.value.detail
    # a good run logs nothing secret either
    caplog.clear()
    bench.bands["COM5"].errors.clear()
    usb.set_wifi("COM5", SSID, PASSWORD, timeout=FAST)
    assert PASSWORD not in caplog.text and SSID not in caplog.text


# ---- the real opener, with pyserial itself faked ----------------------------------

def test_open_serial_sets_dtr_and_rts_before_the_open(monkeypatch):
    serial = pytest.importorskip("serial")
    events = []

    class Rec:
        def __init__(self):
            events.append(("new",))

        def __setattr__(self, name, value):
            events.append(("set", name, value))

        def open(self):
            events.append(("open",))

    monkeypatch.setattr(serial, "Serial", Rec)
    usb._open_serial("COM5")
    names = [e[1] if e[0] == "set" else e[0] for e in events]
    i_open = names.index("open")
    assert ("set", "dtr", True) in events[:i_open]
    assert ("set", "rts", False) in events[:i_open]
    assert ("set", "baudrate", 115200) in events
    assert ("set", "port", "COM5") in events
    # nothing toggles the control lines after the open
    assert not [e for e in events[i_open + 1:] if e[0] == "set"]


def test_open_serial_permission_denied_on_linux_names_the_dialout_group(monkeypatch):
    serial = pytest.importorskip("serial")

    class Denied:
        def __setattr__(self, name, value):
            pass

        def open(self):
            raise serial.SerialException(
                "could not open port '/dev/ttyACM0': PermissionError(13, 'Permission denied')")

    monkeypatch.setattr(serial, "Serial", Denied)
    monkeypatch.setattr(usb.sys, "platform", "linux")
    with pytest.raises(usb.UsbSetupError) as e:
        usb._open_serial("/dev/ttyACM0")
    assert e.value.code == "no_port" and "dialout" in e.value.message
    monkeypatch.setattr(usb.sys, "platform", "win32")
    with pytest.raises(usb.UsbSetupError) as e:
        usb._open_serial("COM5")
    assert "dialout" not in e.value.message and "serial monitor" in e.value.message


def test_open_serial_failure_is_no_port(monkeypatch):
    serial = pytest.importorskip("serial")

    class Boom:
        def __setattr__(self, name, value):
            pass

        def open(self):
            raise serial.SerialException("could not open port 'COM5': PermissionError(13)")

    monkeypatch.setattr(serial, "Serial", Boom)
    with pytest.raises(usb.UsbSetupError) as e:
        usb._open_serial("COM5")
    assert e.value.code == "no_port" and "COM5" in e.value.message

"""A fake USB bus for the Wi-Fi-over-USB tests: FakeBand speaks the band's side
of the line protocol and FakeSerial stands in for a pyserial port. Nothing here
opens a real serial device."""

import json
import time

from openmuscle.provision import usb

PASSWORD = "hunter2-correct-horse"
SSID = "My Home WiFi"
BAND_ID = "flexgrid-d7af0b"


class FakeBand:
    """The band's side of the protocol. Behaviour switches:

    noise         log lines the band prints before every reply
    silent        never answers
    silent_first  ignore this many commands, then answer
    late_first    the reply to the first command is released only when the
                  second command arrives (so it is late, and the second command
                  is the one that is ignored)
    wrong_id      send an ack with another id before the real one
    errors        {verb: (code, message)} answer ok:false for that verb
    mute_verbs    verbs the band never answers
    """

    def __init__(self, band_id=BAND_ID, role="left", state="unprovisioned",
                 wifi_ssid="", noise=(), silent=False, silent_first=0,
                 late_first=False, wrong_id=False, errors=None, mute_verbs=()):
        self.info = {"id": band_id, "role": role, "fw": "1.2.3", "state": state,
                     "wifi_ssid": wifi_ssid, "ip": "", "usb_console": 1}
        self.noise = list(noise)
        self.silent = silent
        self.silent_first = silent_first
        self.late_first = late_first
        self.wrong_id = wrong_id
        self.errors = dict(errors or {})
        self.mute_verbs = set(mute_verbs)
        self.received = []          # parsed command objects
        self.opened = 0
        self.closed = 0

    def reply_for(self, cmd):
        verb = cmd["data"]["verb"]
        base = {"v": "1.0", "type": "ack", "id": cmd["id"]}
        if verb in self.errors:
            code, msg = self.errors[verb]
            return dict(base, ok=False, error=code, message=msg)
        if verb == "get_info":
            return dict(base, ok=True, data=dict(self.info))
        if verb in ("set_wifi", "forget_wifi"):
            return dict(base, ok=True, data={"restart": True})
        if verb == "set_role":
            self.info["role"] = cmd["data"]["role"]
            return dict(base, ok=True, data={"role": cmd["data"]["role"]})
        return dict(base, ok=False, error="unknown_verb", message="no such verb")


class FakeSerial:
    def __init__(self, band, fail_read=False, fail_write=False):
        self.band = band
        self.out = []               # lines the "computer" will read
        self.held = None            # a late reply waiting for the second command
        self.n = 0
        self.fail_read = fail_read
        self.fail_write = fail_write
        self.is_closed = False
        band.opened += 1

    def reset_input_buffer(self):
        self.out.clear()

    def flush(self):
        pass

    def write(self, data):
        if self.fail_write:
            raise OSError("device disconnected")
        assert data.endswith(b"\n") and data.count(b"\n") == 1
        cmd = json.loads(data.decode("utf-8"))
        assert cmd["v"] == "1.0" and cmd["type"] == "cmd" and cmd["id"]
        self.band.received.append(cmd)
        self.n += 1
        b = self.band
        if self.held is not None:
            self.out.append(self.held)          # the late reply lands now
            self.held = None
            return len(data)
        if (b.silent or self.n <= b.silent_first
                or cmd["data"]["verb"] in b.mute_verbs):
            return len(data)
        for line in b.noise:
            self.out.append((line + "\r\n").encode())
        reply = (json.dumps(b.reply_for(cmd)) + "\r\n").encode()
        if b.wrong_id:
            wrong = dict(b.reply_for(cmd), id="someone-else")
            self.out.append((json.dumps(wrong) + "\r\n").encode())
        if b.late_first and self.n == 1:
            self.held = reply
            return len(data)
        self.out.append(reply)
        return len(data)

    def readline(self):
        if self.fail_read:
            raise OSError("device disconnected")
        if self.out:
            return self.out.pop(0)
        time.sleep(0.01)
        return b""

    def close(self):
        self.is_closed = True
        self.band.closed += 1


class FakePort:
    def __init__(self, device, vid=usb.USB_VID, pid=usb.USB_PID, description="USB Serial"):
        self.device, self.vid, self.pid, self.description = device, vid, pid, description


class Bench:
    """A fake USB bus: bench.add('COM5', band) plugs a band in."""

    def __init__(self):
        self.ports = []         # FakePort
        self.bands = {}         # device -> FakeBand
        self.fail_open = set()

    def add(self, device, band, **portkw):
        self.ports.append(FakePort(device, **portkw))
        self.bands[device] = band
        return band


def install_bench(monkeypatch):
    """Point usb.py at a Bench instead of pyserial and return it."""
    b = Bench()

    def open_serial(port):
        if port in b.fail_open or port not in b.bands:
            raise usb.UsbSetupError("no_port", "Could not open the USB port %s." % port)
        return FakeSerial(b.bands[port])

    monkeypatch.setattr(usb, "_comports", lambda: list(b.ports))
    monkeypatch.setattr(usb, "_open_serial", open_serial)
    return b

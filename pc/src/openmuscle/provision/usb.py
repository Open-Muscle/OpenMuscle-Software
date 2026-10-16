"""Wi-Fi setup over the band's USB cable.

A band answers a small command channel on its USB serial port, so this computer
can hand it the home Wi-Fi name and password with no setup network at all. The
band then restarts and joins that Wi-Fi, and announces itself on the LAN like any
other band (openmuscle.provision.client.wait_for_device finds it by id).

The wire format is one JSON object per line, newline terminated, 115200 baud:

    request   {"v":"1.0","type":"cmd","id":"<short>","data":{"verb":"get_info"}}
    reply     {"v":"1.0","type":"ack","id":"<same>","ok":true,"data":{...}}
              {"v":"1.0","type":"ack","id":"<same>","ok":false,
               "error":"<code>","message":"<plain words>"}

Verbs: get_info, set_wifi {ssid, password}, forget_wifi, set_role {role}.
set_wifi and forget_wifi answer ok with {restart: true} and the band reboots
about a second later; it re-enumerates and may come back under another port name.

The band also prints log lines on the same port. Every line that does not start
with "{" is ignored, replies are matched by id, and a command gets 3 seconds
(get_info is tried up to three times).

Two port rules: DTR stays asserted and RTS is never pulsed (RTS is wired to the
chip's reset), so the port is opened with those lines set before the open.

    list_ports()                 bands on USB, with their id/role/state if they answer
    info(port)                   get_info
    set_wifi(port, ssid, pw)     hand over Wi-Fi details; the band then restarts
    forget_wifi(port)            clear them; the band restarts into setup mode
    set_role(port, role)         left / right / labeler

The Wi-Fi password is never logged, never put in an exception, and is not part
of any repr. Lines received from the band are not logged either.
"""

import itertools
import json
import logging
import threading
import time
from dataclasses import dataclass, field

from openmuscle.provision.client import validate_credentials

logger = logging.getLogger(__name__)

USB_VID = 0x303A                 # Espressif
USB_PID = 0x4001                 # the band's USB serial port
BAUD = 115200
PROTOCOL_VERSION = "1.0"
COMMAND_TIMEOUT_S = 3.0          # per command
INFO_TRIES = 3                   # get_info: the first try plus two retries
POLL_S = 0.25                    # one serial read waits at most this long
RESTART_WAIT_S = 20              # a band announces on the LAN within about this long
ROLES = ("left", "right", "labeler")
MAX_LINE_BYTES = 16384           # a runaway line without a newline is dropped

# Machine codes -> text a person can act on.
_MESSAGES = {
    "no_port": ("No band was found on a USB port. Plug the band into this computer "
                "with a USB cable that carries data (some cables only charge), then "
                "try again."),
    "many_ports": ("More than one band is plugged in. Unplug all but the one you "
                   "want to set up, then try again."),
    "no_answer": ("The band did not answer on the USB cable. Unplug the cable and "
                  "plug it back in (or press the BOOT button), then try again."),
    "rejected": "The band did not accept that request.",
}

# What the band's own error codes mean to a person. The band's message is only
# used for a code that is not listed here.
_BAND_MESSAGES = {
    "bad_ssid": "The band did not accept that Wi-Fi name. It must be 1 to 32 characters.",
    "bad_password": ("The band did not accept that Wi-Fi password. It must be 8 to 63 "
                     "characters, or empty for a network with no password."),
    "save_failed": ("The band could not save the Wi-Fi details. Unplug the cable, "
                    "plug it back in, and try again."),
    "bad_json": ("The band could not read the request (it was garbled on the way). "
                 "Please try again."),
    "unknown_verb": ("This band's software does not know how to be set up over USB. "
                     "Use the band's own Wi-Fi instead."),
}

_port_lock = threading.Lock()    # one conversation with a band at a time
_ids = itertools.count(1)


class UsbSetupError(Exception):
    """A USB setup step failed. `code` is stable and machine readable (no_port,
    many_ports, no_answer, rejected), `message` is plain English, `band_code` and
    `band_message` carry the band's own error for `rejected`, `detail` is extra
    text for a log (never the Wi-Fi password)."""

    def __init__(self, code, message=None, band_code="", band_message="", detail=""):
        self.code = code
        self.message = message or _MESSAGES.get(code, code)
        self.band_code = band_code
        self.band_message = band_message
        self.detail = detail
        super().__init__(self.message)

    def __str__(self):
        return self.message

    def __repr__(self):
        return "UsbSetupError(code=%r, band_code=%r, message=%r)" % (
            self.code, self.band_code, self.message)


def _err(code, **kw):
    return UsbSetupError(code, _MESSAGES.get(code, code), **kw)


@dataclass
class BandInfo:
    """What get_info says about a band on USB."""
    band_id: str
    role: str = ""
    fw: str = ""
    state: str = ""               # "unprovisioned" or "provisioned"
    wifi_ssid: str = ""
    ip: str = ""
    usb_console: int = 0
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def provisioned(self):
        return self.state == "provisioned"

    @classmethod
    def from_payload(cls, d):
        d = d or {}
        try:
            console = int(d.get("usb_console") or 0)
        except (TypeError, ValueError):
            console = 0
        return cls(
            band_id=str(d.get("id") or ""),
            role=str(d.get("role") or ""),
            fw=str(d.get("fw") or ""),
            state=str(d.get("state") or ""),
            wifi_ssid=str(d.get("wifi_ssid") or ""),
            ip=str(d.get("ip") or ""),
            usb_console=console,
            raw=dict(d),
        )


@dataclass
class UsbPort:
    """A USB serial port that belongs to a band, and what it said when asked."""
    port: str
    description: str = ""
    answered: bool = False
    band_id: str = ""
    role: str = ""
    state: str = ""
    fw: str = ""
    wifi_ssid: str = ""

    def to_dict(self):
        return {"port": self.port, "description": self.description,
                "answered": self.answered, "id": self.band_id, "role": self.role,
                "state": self.state, "fw": self.fw, "wifi_ssid": self.wifi_ssid}


@dataclass
class UsbResult:
    """The band accepted a set_wifi or forget_wifi and is about to restart."""
    band_id: str
    role: str = ""
    restart: bool = True
    completed_at: float = 0.0     # time.time() when the ack arrived


# ---------------------------------------------------------------------------
# Seams (tests replace these; nothing else touches pyserial)
# ---------------------------------------------------------------------------

def _comports():
    """pyserial's list of serial ports. Enumerating does not open any of them."""
    try:
        from serial.tools import list_ports as _lp
    except ImportError:
        raise UsbSetupError(
            "no_port",
            "The USB part of OpenMuscle is not installed. Run the OpenMuscle "
            "installer again, then try again.", detail="pyserial is missing")
    return list(_lp.comports())


def _open_serial(port):
    """Open `port` at 115200 baud with DTR asserted and RTS low, set before the
    open so the lines never move afterwards. Raises UsbSetupError(no_port) when
    the port cannot be opened. The object needs write(bytes), readline() (empty
    bytes on timeout), reset_input_buffer() and close()."""
    try:
        import serial
    except ImportError:
        raise UsbSetupError(
            "no_port",
            "The USB part of OpenMuscle is not installed. Run the OpenMuscle "
            "installer again, then try again.", detail="pyserial is missing")
    try:
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = BAUD
        ser.timeout = POLL_S
        ser.write_timeout = COMMAND_TIMEOUT_S
        ser.dtr = True
        ser.rts = False
        ser.open()
    except (OSError, ValueError, serial.SerialException) as e:
        raise UsbSetupError(
            "no_port",
            "Could not open the USB port %s. Another program may be using the band "
            "(close any serial monitor), or the cable was unplugged. Unplug the "
            "cable, plug it back in, and try again." % port,
            detail="%s: %s" % (type(e).__name__, e))
    return ser


# ---------------------------------------------------------------------------
# One conversation with a band
# ---------------------------------------------------------------------------

def _new_id():
    return "om%d" % next(_ids)


def _command_line(cid, verb, **fields):
    msg = {"v": PROTOCOL_VERSION, "type": "cmd", "id": cid,
           "data": dict({"verb": verb}, **fields)}
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class _Session:
    """An open band port. Use as a context manager so the port always closes."""

    def __init__(self, port, timeout=COMMAND_TIMEOUT_S):
        self.port = port
        self.timeout = timeout
        self.ser = None

    def __enter__(self):
        self.ser = _open_serial(self.port)
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass            # the band may already have restarted and gone
            self.ser = None
        return False

    def _read_ack(self, ids, deadline):
        """The first ack whose id is in `ids`, or None at the deadline. Log
        lines, other JSON, and acks for other ids (a reply that came late for
        an earlier command) are skipped."""
        buf = b""
        while time.monotonic() < deadline:
            try:
                chunk = self.ser.readline()
            except Exception as e:
                raise _err("no_answer", detail="read failed: %s" % type(e).__name__)
            if not chunk:
                continue
            buf += chunk
            if not buf.endswith(b"\n"):
                if len(buf) > MAX_LINE_BYTES:
                    buf = b""
                continue            # a partial line; the rest comes on the next read
            line = buf.decode("utf-8", "replace").strip()
            buf = b""
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if (isinstance(obj, dict) and obj.get("type") == "ack"
                    and obj.get("id") in ids):
                return obj
        return None

    def command(self, verb, tries=1, **fields):
        """Send one command and return the ack dict. Raises UsbSetupError:
        no_answer when nothing matching came back in time (after `tries`
        attempts), rejected when the band answered ok:false."""
        ids = set()
        for _ in range(max(1, tries)):
            cid = _new_id()
            ids.add(cid)
            try:
                self.ser.write(_command_line(cid, verb, **fields))
                flush = getattr(self.ser, "flush", None)
                if flush:
                    flush()
            except Exception as e:
                raise _err("no_answer", detail="write failed: %s" % type(e).__name__)
            ack = self._read_ack(ids, time.monotonic() + self.timeout)
            if ack is not None:
                return _check_ack(ack)
        raise _err("no_answer", detail="no reply to %s" % verb)

    def get_info(self, tries=INFO_TRIES):
        ack = self.command("get_info", tries=tries)
        data = ack.get("data")
        info = BandInfo.from_payload(data if isinstance(data, dict) else {})
        if not info.band_id:
            raise _err("no_answer", detail="get_info reply had no id")
        return info


def _check_ack(ack):
    if ack.get("ok") is True:
        return ack
    code = str(ack.get("error") or "")
    msg = str(ack.get("message") or "")
    text = _BAND_MESSAGES.get(code) or msg or _MESSAGES["rejected"]
    raise UsbSetupError("rejected", text, band_code=code, band_message=msg)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_ports():
    """Every USB serial port with the band's VID and PID, as a list of UsbPort
    that have not been asked anything yet. Sorted by port name."""
    out = []
    for p in _comports():
        if getattr(p, "vid", None) == USB_VID and getattr(p, "pid", None) == USB_PID:
            out.append(UsbPort(port=str(p.device),
                               description=str(getattr(p, "description", "") or "")))
    out.sort(key=lambda p: p.port)
    return out


def list_ports(probe=True, timeout=COMMAND_TIMEOUT_S, tries=INFO_TRIES):
    """The bands on USB. With probe=True each port is asked get_info, and the ones
    that answer carry the band's id, role, state and Wi-Fi name. A port that does
    not answer is still listed (answered=False): the band is plugged in but
    silent, which the caller should tell the user to fix by replugging."""
    ports = find_ports()
    if not probe:
        return ports
    for p in ports:
        try:
            with _port_lock:
                with _Session(p.port, timeout) as s:
                    info = s.get_info(tries)
        except UsbSetupError as e:
            logger.debug("usb: %s did not answer (%s)", p.port, e.code)
            continue
        p.answered = True
        p.band_id = info.band_id
        p.role = info.role
        p.state = info.state
        p.fw = info.fw
        p.wifi_ssid = info.wifi_ssid
    return ports


def pick_port(ports):
    """The one port to use from a list_ports() result, or a UsbSetupError that
    says why not: no_port (nothing plugged in), many_ports (more than one band
    answers), no_answer (a band is plugged in but none answers)."""
    answering = [p for p in ports if p.answered]
    if len(answering) == 1:
        return answering[0]
    if len(answering) > 1:
        raise _err("many_ports")
    if ports:
        raise _err("no_answer")
    raise _err("no_port")


def info(port, timeout=COMMAND_TIMEOUT_S):
    """get_info on `port`. Raises UsbSetupError (no_port, no_answer, rejected)."""
    with _port_lock:
        with _Session(port, timeout) as s:
            return s.get_info()


def _restarting_command(port, verb, expect_id, timeout, **fields):
    """get_info first (so the caller learns which band this is and can refuse the
    wrong one), then `verb`. Once the band acks it reboots, so the port is closed
    straight away and any error from the close is ignored."""
    with _port_lock:
        with _Session(port, timeout) as s:
            before = s.get_info()
            if expect_id and before.band_id != expect_id:
                raise UsbSetupError(
                    "rejected",
                    "A different band answered on that USB port (%s). Make sure "
                    "only the band you are setting up is plugged in, then try again."
                    % before.band_id, band_code="wrong_device")
            s.command(verb, tries=1, **fields)
            return UsbResult(band_id=before.band_id, role=before.role, restart=True,
                             completed_at=time.time())


def set_wifi(port, ssid, password, expect_id="", timeout=COMMAND_TIMEOUT_S, validate=True):
    """Give the band at `port` the home Wi-Fi name and password. The band saves
    them, answers, and restarts about a second later; it joins the network and
    announces itself on the LAN within about 20 seconds.

    Raises ProvisionError (ssid_length_invalid, password_length_invalid) before
    anything is sent, and UsbSetupError otherwise. `expect_id` makes the call
    refuse (rejected, band_code wrong_device) if a different band is on the port.
    Returns UsbResult with the band's id."""
    if validate:
        validate_credentials(ssid, password)
    logger.info("usb: sending Wi-Fi details to the band on %s", port)
    return _restarting_command(port, "set_wifi", expect_id, timeout,
                               ssid=ssid, password=password or "")


def forget_wifi(port, expect_id="", timeout=COMMAND_TIMEOUT_S):
    """Make the band forget its Wi-Fi. It restarts into setup mode."""
    logger.info("usb: clearing the Wi-Fi on the band on %s", port)
    return _restarting_command(port, "forget_wifi", expect_id, timeout)


def set_role(port, role, timeout=COMMAND_TIMEOUT_S):
    """Set the band's role (left, right or labeler). The band does not restart."""
    role = (role or "").strip().lower()
    if role not in ROLES:
        raise UsbSetupError("rejected", "The role must be left, right or labeler.",
                            band_code="bad_role")
    with _port_lock:
        with _Session(port, timeout) as s:
            before = s.get_info()
            s.command("set_role", tries=1, role=role)
            return UsbResult(band_id=before.band_id, role=role, restart=False,
                             completed_at=time.time())

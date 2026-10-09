"""Stdlib client for the device-side provisioning HTTP server.

A device with no Wi-Fi credentials brings up its own WPA2 access point
(OM-<dev>-<id-tail>, address 192.168.4.1) and serves a tiny HTTP server on port
80. This module is the PC side of that handshake:

    probe_ap()/get_info(host)   GET  /info          who is this device, what state
    scan(host)                  GET  /scan          nearby 2.4 GHz networks (optional)
    provision(host, ssid, pw)   POST /provision     hand over the home Wi-Fi creds
    reprovision(host)           POST /reprovision   clear creds, back to setup mode
    wait_for_device(id, ...)    poll the discovery registry for the device on the LAN

Two firmware quirks shape the transport, which is why this does not use
http.client or urllib:

  * The device reads the request with ONE read of up to 2048 bytes. http.client
    sends the header and the body as two separate sends, so the body can arrive
    in a later TCP segment and be dropped (the device then sees an empty body and
    rejects the request). Every request here is built as a single bytes object
    and written with one sendall().
  * The device closes the connection after each response and can reset it while
    it is busy, so a reset (or an empty reply) is retried once.

The Wi-Fi password is never logged, never put in an exception, and is redacted
in every repr.
"""

import json
import logging
import re
import socket
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

AP_HOST = "192.168.4.1"          # the device's address on its own access point
DEFAULT_PORT = 80
DEFAULT_TIMEOUT_S = 5.0
SCAN_TIMEOUT_S = 8.0             # the device may take ~3 s to scan before answering
INFO_TIMEOUT_S = 2.0             # probe_ap is called in a loop; fail fast
MAX_RESPONSE_BYTES = 1 << 20     # the setup page is a few KB; refuse anything absurd
RETRY_DELAY_S = 0.2

SSID_MAX_BYTES = 32
PASSWORD_MIN_BYTES = 8
PASSWORD_MAX_BYTES = 63

# Machine codes -> text a non-engineer can act on. Some take the host.
_MESSAGES = {
    "ssid_length_invalid": "The Wi-Fi name must be 1 to 32 characters",
    "password_length_invalid": "The password must be 8 to 63 characters",
    "invalid_json": ("The device could not read the setup request (it was "
                     "garbled on the way). Please try again."),
    "incomplete_body": ("The device received only part of the setup request. "
                        "Please try again."),
    "not_in_setup_mode": ("This device is already connected to a Wi-Fi network "
                          "and will not accept new Wi-Fi details. Reset its "
                          "Wi-Fi from the device menu or its status page, then "
                          "set it up again."),
    "unreachable": ("Cannot reach the device at {host}. Check that this "
                    "computer is on the device's own OM-... Wi-Fi network (for "
                    "setup) or on the same network as the device (otherwise)."),
    "not_openmuscle": ("Something answered at {host}, but it does not look like "
                       "an OpenMuscle device."),
    "payload_too_large": "The device refused the request because it was too large.",
    "no_route": ("This device's firmware does not support that action. "
                 "Update the firmware or use the device's own setup page."),
    "bad_response": "The device sent a reply this software does not understand.",
    "join_timeout": ("The device did not show up on your Wi-Fi in time. Check "
                     "the Wi-Fi name and password, then try again."),
}


class ProvisionError(Exception):
    """A provisioning step failed. `code` is stable and machine-readable,
    `message` is plain English for the user, `detail` is the raw device text
    (never contains the password), `status` is the HTTP status if there was one."""

    def __init__(self, code, message=None, detail="", status=None):
        self.code = code
        self.message = message or _MESSAGES.get(code, code)
        self.detail = detail
        self.status = status
        super().__init__(self.message)

    def __str__(self):
        return self.message

    def __repr__(self):
        return "ProvisionError(code=%r, message=%r)" % (self.code, self.message)


def _err(code, host="", detail="", status=None):
    return ProvisionError(code, _MESSAGES.get(code, code).format(host=host),
                          detail=detail, status=status)


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------

class Credentials:
    """Wi-Fi name + password with the password kept out of every repr/str."""

    __slots__ = ("ssid", "_password")

    def __init__(self, ssid, password=""):
        self.ssid = ssid
        self._password = password or ""

    @property
    def password(self):
        return self._password

    def __repr__(self):
        return "Credentials(ssid=%r, password='***')" % (self.ssid,)

    __str__ = __repr__


@dataclass
class DeviceInfo:
    """What GET /info says about a device."""
    device_id: str
    device_type: str = ""
    fw: str = ""
    caps: list = field(default_factory=list)
    matrix: list = field(default_factory=list)
    state: str = ""               # "unprovisioned" (AP) or "provisioned" (LAN)
    last_wifi_error: str = ""     # why the last join attempt failed, if the fw says
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def unprovisioned(self):
        return self.state == "unprovisioned"

    @classmethod
    def from_payload(cls, d):
        return cls(
            device_id=str(d.get("id") or ""),
            device_type=str(d.get("dev") or ""),
            fw=str(d.get("fw") or ""),
            caps=list(d.get("caps") or []),
            matrix=list(d.get("matrix") or []),
            state=str(d.get("state") or ""),
            last_wifi_error=str(d.get("last_wifi_error") or ""),
            raw=dict(d),
        )


@dataclass
class ProvisionResult:
    """The device accepted the request and is about to reboot."""
    device_id: str
    reboot_in_ms: int = 0
    next_step: str = ""
    completed_at: float = 0.0     # time.time() when the ack arrived


# ---------------------------------------------------------------------------
# Validation + helpers
# ---------------------------------------------------------------------------

def validate_credentials(ssid, password):
    """Mirror the device's checks so the user gets an answer without a round
    trip. Lengths are UTF-8 bytes, as on the device. An empty password is the
    open-network case. The SSID is not stripped: spaces can be part of a name."""
    if not isinstance(ssid, str) or not (1 <= len(ssid.encode("utf-8")) <= SSID_MAX_BYTES):
        raise _err("ssid_length_invalid")
    pw = password or ""
    if not isinstance(pw, str):
        raise _err("password_length_invalid")
    n = len(pw.encode("utf-8"))
    if pw and not (PASSWORD_MIN_BYTES <= n <= PASSWORD_MAX_BYTES):
        raise _err("password_length_invalid")


def ap_ssid_for(info):
    """The access-point name a device should be broadcasting, from its /info
    (OM-<dev>-<id-tail>). Lets the caller check that the AP it joined is the
    device it asked about. Accepts a DeviceInfo or a /info dict."""
    if isinstance(info, DeviceInfo):
        dev, did = info.device_type, info.device_id
    else:
        dev, did = info.get("dev", ""), info.get("id", "")
    prefix = dev + "-"
    tail = did[len(prefix):] if did.startswith(prefix) else did
    return "OM-%s-%s" % (dev, tail)


_CODE_RE = re.compile(r"[a-z][a-z0-9_]*")


def _device_error_code(message):
    """Firmware messages look like 'ssid_length_invalid (1..32)' or
    'invalid_json: Expecting value'. The leading token is the code."""
    m = _CODE_RE.match((message or "").strip().lower())
    return m.group(0) if m else ""


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _connect(host, port, timeout):
    """Separate seam so tests can observe or wrap the socket."""
    return socket.create_connection((host, port), timeout=timeout)


def _build_request(host, port, method, path, body):
    lines = [
        "%s %s HTTP/1.1" % (method, path),
        "Host: %s" % (host if port == 80 else "%s:%d" % (host, port)),
        "User-Agent: openmuscle-provision",
        "Accept: application/json",
        "Connection: close",
    ]
    if body is not None:
        lines.append("Content-Type: application/json")
    lines.append("Content-Length: %d" % (len(body) if body is not None else 0))
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
    return head + (body or b"")


def _parse_response(raw):
    """(status:int, headers:dict, body:bytes) or ValueError."""
    sep = raw.find(b"\r\n\r\n")
    sep_len = 4
    if sep < 0:
        sep = raw.find(b"\n\n")
        sep_len = 2
        if sep < 0:
            raise ValueError("no header terminator")
    head = raw[:sep].decode("iso-8859-1")
    lines = head.replace("\r", "").split("\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/"):
        raise ValueError("bad status line")
    status = int(parts[1])
    headers = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status, headers, raw[sep + sep_len:]


def _exchange(host, port, request, timeout):
    """One connection: send `request` with a single sendall, read the reply to
    EOF (or Content-Length). Returns raw bytes. Raises ConnectionError for
    resets/empty replies (retryable) and ProvisionError(unreachable) for
    connect failures and timeouts."""
    try:
        sock = _connect(host, port, timeout)
    except OSError as e:
        raise _err("unreachable", host, detail="%s: %s" % (type(e).__name__, e))
    try:
        sock.settimeout(timeout)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            sock.sendall(request)
            chunks = []
            total = 0
            want = None
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise _err("bad_response", host, detail="response too large")
                if want is None:
                    buf = b"".join(chunks)
                    try:
                        _, hdrs, body = _parse_response(buf)
                    except ValueError:
                        continue
                    cl = hdrs.get("content-length")
                    if cl is not None and cl.isdigit():
                        want = len(buf) - len(body) + int(cl)
                if want is not None and total >= want:
                    break
        except socket.timeout:
            raise _err("unreachable", host, detail="timed out waiting for the device")
        except ConnectionError:
            raise
        except OSError as e:
            raise _err("unreachable", host, detail="%s: %s" % (type(e).__name__, e))
        raw = b"".join(chunks)
        if not raw:
            raise ConnectionResetError("device closed the connection without a reply")
        return raw
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _request(host, method, path, body=None, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT_S,
             retries=1):
    """Send one request, retrying `retries` times on a connection reset.
    Returns (status, headers, body_bytes). Never logs the body."""
    request = _build_request(host, port, method, path, body)
    attempt = 0
    while True:
        try:
            raw = _exchange(host, port, request, timeout)
            break
        except ConnectionError as e:
            if attempt >= retries:
                raise _err("unreachable", host,
                           detail="connection reset (%s)" % type(e).__name__)
            attempt += 1
            logger.debug("provision: %s %s reset by %s, retrying", method, path, host)
            time.sleep(RETRY_DELAY_S)
    try:
        status, headers, rbody = _parse_response(raw)
    except ValueError as e:
        raise _err("bad_response", host, detail=str(e))
    logger.debug("provision: %s %s -> %s (%d bytes)", method, path, status, len(rbody))
    return status, headers, rbody


def _json(rbody):
    """Decode a JSON object body or return None."""
    try:
        obj = json.loads(rbody.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _raise_for_device_error(host, status, obj):
    """Turn an error reply into a ProvisionError. Call only when the reply is
    not a success."""
    msg = ""
    if obj:
        data = obj.get("data")
        if isinstance(data, dict):
            msg = str(data.get("message") or "")
        if not msg:
            msg = str(obj.get("message") or obj.get("error") or "")
    code = _device_error_code(msg)
    if status == 413:
        code = "payload_too_large"
    elif status == 404 and code in ("", "no_route"):
        code = "no_route"
    if code not in _MESSAGES:
        raise ProvisionError("device_error",
                             "The device reported an error: %s" % (msg or status),
                             detail=msg, status=status)
    raise _err(code, host, detail=msg, status=status)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_info(host, port=DEFAULT_PORT, timeout=INFO_TIMEOUT_S):
    """GET /info. Works on the device's AP (state 'unprovisioned') and on the
    LAN (state 'provisioned'). Raises ProvisionError(unreachable) if nothing
    answers and ProvisionError(not_openmuscle) if something else does."""
    status, _, body = _request(host, "GET", "/info", port=port, timeout=timeout)
    obj = _json(body)
    if status != 200 or not obj or obj.get("type") != "info" or not obj.get("id"):
        raise _err("not_openmuscle", host, status=status)
    return DeviceInfo.from_payload(obj)


def probe_ap(host=AP_HOST, port=DEFAULT_PORT, timeout=INFO_TIMEOUT_S):
    """get_info against the device's own access point (192.168.4.1)."""
    return get_info(host, port=port, timeout=timeout)


def scan(host=AP_HOST, port=DEFAULT_PORT, timeout=SCAN_TIMEOUT_S):
    """GET /scan: networks the device can see, strongest first, as dicts with
    ssid, rssi, auth. Returns None when the firmware has no /scan (404), so the
    caller can fall back to asking the user to type the name."""
    status, _, body = _request(host, "GET", "/scan", port=port, timeout=timeout)
    if status == 404:
        return None
    obj = _json(body)
    if status != 200:
        if obj:
            _raise_for_device_error(host, status, obj)
        raise _err("bad_response", host, status=status)
    nets = ((obj or {}).get("data") or {}).get("networks")
    if (obj or {}).get("type") != "scan" or not isinstance(nets, list):
        raise _err("bad_response", host, detail="unexpected /scan shape", status=status)
    out = []
    for n in nets:
        if isinstance(n, dict) and n.get("ssid"):
            out.append({"ssid": str(n["ssid"]), "rssi": n.get("rssi"),
                        "auth": str(n.get("auth") or "other")})
    out.sort(key=lambda n: n["rssi"] if isinstance(n["rssi"], (int, float)) else -999,
             reverse=True)
    return out


def provision(host, ssid, password, hub_host=None, hub_port=None,
              port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT_S, validate=True):
    """POST /provision. Hands the home Wi-Fi name and password to a device in
    setup mode; on success the device reboots and joins that network.

    The body is exactly {ssid, password}. hub_host/hub_port are sent only if
    given: while on the device's AP this computer does not know its future LAN
    address, and discovery on the LAN finds the device afterwards. Use them only
    for a fixed, known PC address.

    Returns ProvisionResult. Raises ProvisionError (ssid_length_invalid,
    password_length_invalid, invalid_json, incomplete_body, not_in_setup_mode,
    unreachable, ...). Pass validate=False to let the device do all checking."""
    if validate:
        validate_credentials(ssid, password)
    payload = {"ssid": ssid, "password": password or ""}
    if hub_host:
        payload["hub_host"] = hub_host
        if hub_port:
            payload["hub_port"] = int(hub_port)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    del payload
    logger.info("provision: sending Wi-Fi details to %s (%d byte body)", host, len(body))
    status, _, rbody = _request(host, "POST", "/provision", body=body, port=port,
                                timeout=timeout)
    obj = _json(rbody)
    if status == 200 and obj and obj.get("status") == "ok":
        return ProvisionResult(
            device_id=str(obj.get("id") or ""),
            reboot_in_ms=int(obj.get("reboot_in_ms") or 0),
            next_step=str(obj.get("next") or ""),
            completed_at=time.time(),
        )
    if obj is None and status == 200:
        raise _err("not_openmuscle", host, status=status)
    _raise_for_device_error(host, status, obj)


def reprovision(host, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT_S):
    """POST /reprovision on the device's LAN address: clears its Wi-Fi details
    so it comes back in setup mode (its own OM-... network)."""
    status, _, rbody = _request(host, "POST", "/reprovision", port=port, timeout=timeout)
    obj = _json(rbody)
    if status == 200 and obj and obj.get("status") == "ok":
        return ProvisionResult(
            device_id=str(obj.get("id") or ""),
            reboot_in_ms=int(obj.get("reboot_in_ms") or 0),
            next_step="setup_mode",
            completed_at=time.time(),
        )
    if obj is None and status == 200:
        raise _err("not_openmuscle", host, status=status)
    _raise_for_device_error(host, status, obj)


def wait_for_device(device_id, timeout=60.0, discovery=None, poll_s=0.5, since=None):
    """Block until `device_id` shows up on the LAN, or raise
    ProvisionError(join_timeout).

    `discovery` is anything with snapshot() returning the DiscoveryManager's
    list of device dicts (device_id, ip, age_s, ...). The device counts only if
    it was heard from at or after `since` (default: now), so a stale cache entry
    from before the reboot does not end the wait early. Returns that device's
    snapshot dict.
    """
    if discovery is None:
        raise ValueError("wait_for_device needs a discovery manager (or any "
                         "object with snapshot())")
    start = time.monotonic()
    since = time.time() if since is None else since
    while True:
        now = time.time()
        for d in discovery.snapshot() or []:
            if d.get("device_id") != device_id:
                continue
            age = d.get("age_s")
            if age is not None and now - age >= since - 0.05:
                return d
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            raise _err("join_timeout", detail="%s not seen within %.0fs" % (device_id, timeout))
        time.sleep(min(poll_s, remaining))

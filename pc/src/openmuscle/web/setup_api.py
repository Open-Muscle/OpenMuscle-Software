"""Loopback-only routes behind the /setup wizard.

The wizard page runs in the user's browser; a band or labeler in setup mode is
only reachable from this computer (its own Wi-Fi network, 192.168.4.1), so the
page talks to the device through these thin proxy routes, which call the
stdlib client in openmuscle.provision.

    GET  /api/setup/probe        is a device's setup server answering?
    GET  /api/setup/scan         Wi-Fi networks the device can see (optional)
    POST /api/setup/provision    hand the user's Wi-Fi name + password to it
    POST /api/setup/reprovision  put an already-online device back in setup mode
    GET  /api/setup/wait         did the device show up on the user's network?
    GET  /api/setup/wifi-hint    the Wi-Fi network this computer is on, if known

Rules every route follows:

  * Only a client on this computer (127.0.0.1 / ::1) is served, even under
    --debug where the server is otherwise LAN-open. A browser Origin that is not
    local is refused too, so another web page cannot drive the wizard.
  * The device address is limited to the setup network 192.168.4.0/24. The one
    exception is reprovision, which uses the address discovery already holds.
  * The Wi-Fi name and password travel only in the JSON body. They are never
    put in a log line, the log buffer or the access log, and validation errors
    never echo them back.
"""

import asyncio
import ipaddress
import logging
import os
import re
import subprocess
import sys
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from openmuscle.discovery.manager import default_lan_ip
from openmuscle.provision import client as pclient
from openmuscle.provision.client import ProvisionError

SETUP_NET = ipaddress.ip_network("192.168.4.0/24")
LOCAL_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "::1"}
WAIT_S = 60                   # how long the UI should keep waiting before it offers help
MAX_WAIT_POLL_S = 30.0        # one /wait call never blocks longer than this
ENV_AP = "OPENMUSCLE_SETUP_AP"   # dev only: "127.0.0.1:PORT" stands in for 192.168.4.1:80

# Error code -> HTTP status for the JSON {ok: false, code, message} replies.
_STATUS = {
    "invalid_request": 400,
    "ssid_length_invalid": 400,
    "password_length_invalid": 400,
    "invalid_json": 400,
    "incomplete_body": 400,
    "host_not_allowed": 400,
    "wrong_device": 409,
    "not_in_setup_mode": 409,
    "unknown_device": 404,
    "discovery_disabled": 409,
}


# ---------------------------------------------------------------------------
# Keeping the Wi-Fi details out of every log
# ---------------------------------------------------------------------------

class SecretScrubFilter(logging.Filter):
    """Replace any remembered secret (and any ssid=/password= pair) in a log
    record with ***. The routes never log the details themselves, so this is a
    second lock: it also covers a library or traceback line that would."""

    _PAIR = re.compile(r"""(?i)\b(ssid|password|passwd|psk)(["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,&;}]+)""")

    def __init__(self):
        super().__init__()
        self._secrets = []
        self._lock = threading.Lock()

    def add(self, *values):
        with self._lock:
            for v in values:
                if isinstance(v, str) and len(v) >= 3 and v not in self._secrets:
                    self._secrets.append(v)
            del self._secrets[:-16]       # bounded; only the recent ones matter

    def scrub(self, text):
        with self._lock:
            secrets = list(self._secrets)
        for s in secrets:
            text = text.replace(s, "***")
        def _mask(m):
            val = m.group(3)
            keep = val[0] if val[:1] in ('"', "'") else ""
            return m.group(1) + m.group(2) + keep + "***" + keep
        return self._PAIR.sub(_mask, text)

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = self.scrub(msg)
        if clean != msg:
            record.msg = clean
            record.args = None
        return True


def install_scrubber(state):
    """Attach one SecretScrubFilter to the log-buffer handler and to the uvicorn
    loggers. Returns the filter (the routes register each new secret on it)."""
    flt = SecretScrubFilter()
    for h in logging.getLogger().handlers:
        if getattr(h, "buffer", None) is state.log_buffer:
            h.addFilter(flt)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(flt)
    return flt


# ---------------------------------------------------------------------------
# Which Wi-Fi network is this computer on? (best effort, never raises)
# ---------------------------------------------------------------------------

def _decode(raw):
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("mbcs" if sys.platform.startswith("win") else "latin-1",
                          "replace")


def _run(cmd, timeout=3.0):
    """Run a small read-only OS command and return its text output."""
    kw = {}
    if sys.platform.startswith("win"):
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, **kw)
    return _decode(r.stdout)


def parse_netsh(out):
    """`netsh wlan show interfaces`: the first 'SSID : name' line (BSSID lines
    do not match because the pattern is anchored at the key)."""
    for line in (out or "").splitlines():
        m = re.match(r"^\s*SSID\s*:\s*(.*?)\s*$", line)
        if m and m.group(1):
            return m.group(1)
    return None


def parse_nmcli(out):
    """`nmcli -t -f active,ssid dev wifi`: 'yes:Name' (colons in the name are
    escaped as \\:)."""
    for line in (out or "").splitlines():
        if not line.startswith("yes:"):
            continue
        name = line[4:].replace("\\:", ":").replace("\\\\", "\\")
        if name:
            return name
    return None


def parse_networksetup(out):
    """`networksetup -getairportnetwork en0`: 'Current Wi-Fi Network: Name'."""
    m = re.search(r"Current (?:Wi-Fi|AirPort) Network:\s*(.+)", out or "")
    if not m:
        return None
    name = m.group(1).strip()
    if not name or name.lower() in ("<redacted>", "redacted"):
        return None
    return name


def wifi_hint(platform=None, run=None):
    """The name of the Wi-Fi network this computer is on, or None. `platform`
    and `run` exist so tests can feed canned command output."""
    platform = platform or sys.platform
    run = run or _run
    try:
        if platform.startswith("win"):
            return parse_netsh(run(["netsh", "wlan", "show", "interfaces"]))
        if platform == "darwin":
            for dev in ("en0", "en1"):
                name = parse_networksetup(run(["networksetup", "-getairportnetwork", dev]))
                if name:
                    return name
            return None
        return parse_nmcli(run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"]))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Request guards
# ---------------------------------------------------------------------------

def is_loopback(host):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _host_name(host_header):
    """The host part of a Host header value, without port or [] brackets."""
    h = host_header.strip().lower()
    if h.startswith("["):
        return h[1:].split("]", 1)[0]
    if h.count(":") == 1:
        return h.split(":", 1)[0]
    return h


def require_local(request: Request):
    """403 unless the request comes from this computer's own browser."""
    client = request.client.host if request.client else ""
    if not is_loopback(client):
        raise HTTPException(status_code=403,
                            detail="Device setup only works from the computer running OpenMuscle.")
    # A DNS-rebinding page reaches this server under its own host name with no
    # Origin header, so the Host header has to be a local name as well.
    host_header = request.headers.get("host")
    if host_header and _host_name(host_header) not in LOCAL_ORIGIN_HOSTS:
        raise HTTPException(status_code=403, detail="Cross-site request refused.")
    origin = request.headers.get("origin")
    if origin and origin != "null":
        try:
            host = urlparse(origin).hostname or ""
        except ValueError:
            host = ""
        if host not in LOCAL_ORIGIN_HOSTS:
            raise HTTPException(status_code=403, detail="Cross-site request refused.")
    elif origin == "null":
        raise HTTPException(status_code=403, detail="Cross-site request refused.")


def _fail(code, message, status=None, **extra):
    out = {"ok": False, "code": code, "message": message}
    out.update(extra)
    return JSONResponse(out, status_code=status or _STATUS.get(code, 502))


def _fail_from(e: ProvisionError):
    return _fail(e.code, e.message)


class SetupProvisionBody(BaseModel):
    ssid: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=128)
    host: Optional[str] = Field(default=None, max_length=64)
    expect_id: Optional[str] = Field(default=None, max_length=64)


class ReprovisionBody(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)


async def _read_json(request: Request, model):
    """Parse the body into `model` without ever echoing it back."""
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return model(**data), None
    except (ValueError, TypeError, ValidationError):
        return None, _fail("invalid_request",
                           "The request was incomplete or too long. "
                           "Check the Wi-Fi name and password and try again.")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register(app, state):
    """Add the /api/setup/* routes to `app`. `state` is the AppState."""
    scrub = install_scrubber(state)
    app.state.setup_scrub = scrub
    override = os.environ.get(ENV_AP, "").strip()
    app.state.setup_ap_override = None
    if override:
        h, _, p = override.partition(":")
        try:
            app.state.setup_ap_override = (h, int(p or 80))
        except ValueError:
            pass
    pending = {}        # device_id -> time the device accepted its Wi-Fi details

    def target(host):
        """(host, port) for a device in setup mode, or an error response."""
        ov = app.state.setup_ap_override
        if ov:
            return ov, None
        host = (host or pclient.AP_HOST).strip()
        try:
            ok = ipaddress.ip_address(host) in SETUP_NET
        except ValueError:
            ok = False
        if not ok:
            return None, _fail("host_not_allowed",
                               "Setup only talks to a device on its own Wi-Fi network.")
        return (host, pclient.DEFAULT_PORT), None

    def info_dict(info):
        return {"id": info.device_id, "dev": info.device_type, "fw": info.fw,
                "state": info.state, "last_wifi_error": info.last_wifi_error,
                "ap_name": pclient.ap_ssid_for(info)}

    @app.get("/api/setup/probe")
    async def setup_probe(request: Request, host: Optional[str] = None):
        require_local(request)
        tgt, err = target(host)
        if err is not None:
            return err
        try:
            info = await asyncio.to_thread(pclient.get_info, tgt[0], tgt[1],
                                           pclient.INFO_TIMEOUT_S)
        except ProvisionError as e:
            return {"reachable": False, "reason": e.code, "message": e.message}
        return {"reachable": True, "info": info_dict(info)}

    @app.get("/api/setup/scan")
    async def setup_scan(request: Request, host: Optional[str] = None):
        require_local(request)
        tgt, err = target(host)
        if err is not None:
            return err
        try:
            nets = await asyncio.to_thread(pclient.scan, tgt[0], tgt[1])
        except ProvisionError as e:
            return {"supported": False, "networks": [], "reason": e.code,
                    "message": e.message}
        if nets is None:
            return {"supported": False, "networks": []}
        return {"supported": True, "networks": nets}

    @app.post("/api/setup/provision")
    async def setup_provision(request: Request):
        require_local(request)
        body, err = await _read_json(request, SetupProvisionBody)
        if err is not None:
            return err
        scrub.add(body.ssid, body.password)
        tgt, err = target(body.host)
        if err is not None:
            return err
        host, port = tgt
        if body.expect_id:
            try:
                info = await asyncio.to_thread(pclient.get_info, host, port,
                                               pclient.INFO_TIMEOUT_S)
            except ProvisionError as e:
                return _fail_from(e)
            if info.device_id != body.expect_id:
                return _fail("wrong_device",
                             "A different device answered (%s). Make sure only the "
                             "device you are setting up is in setup mode, then try again."
                             % info.device_id)
        try:
            res = await asyncio.to_thread(pclient.provision, host, body.ssid,
                                          body.password, None, None, port)
        except ProvisionError as e:
            return _fail_from(e)
        pending[res.device_id] = res.completed_at
        state.log_buffer.info("setup", "sent Wi-Fi details to %s" % (res.device_id or "device"))
        return {"ok": True, "id": res.device_id, "reboot_in_ms": res.reboot_in_ms,
                "wait_s": WAIT_S, "since": res.completed_at}

    @app.post("/api/setup/reprovision")
    async def setup_reprovision(request: Request):
        require_local(request)
        body, err = await _read_json(request, ReprovisionBody)
        if err is not None:
            return err
        if not state.discovery:
            return _fail("discovery_disabled", "Device discovery is turned off.")
        dev = next((d for d in state.discovery.snapshot()
                    if d.get("device_id") == body.device_id), None)
        if dev is None or not dev.get("ip"):
            return _fail("unknown_device",
                         "That device has not been seen on this network yet.")
        ip = dev["ip"]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            addr = None
        if addr is None or not addr.is_private:
            return _fail("host_not_allowed", "Only a device on your own network can be reset.")
        port = pclient.DEFAULT_PORT
        ov = app.state.setup_ap_override
        if ov and ov[0] == ip:
            port = ov[1]
        try:
            res = await asyncio.to_thread(pclient.reprovision, ip, port)
        except ProvisionError as e:
            return _fail_from(e)
        state.log_buffer.info("setup", "reset Wi-Fi on %s" % body.device_id)
        return {"ok": True, "id": res.device_id or body.device_id,
                "reboot_in_ms": res.reboot_in_ms}

    @app.get("/api/setup/wait")
    async def setup_wait(request: Request, device_id: str = "", timeout: float = 5.0,
                         since: Optional[float] = None):
        """Wait up to `timeout` s (max 30) for `device_id` to announce on the
        user's network. The UI calls this in a loop, so each call returns soon
        and the page can keep its checklist live. `since` is the `since` value
        the provision call returned: a sighting before it is the old network."""
        require_local(request)
        if not state.discovery:
            return _fail("discovery_disabled", "Device discovery is turned off.")
        if not device_id or len(device_id) > 64:
            return _fail("invalid_request", "No device was named.")
        timeout = max(0.0, min(float(timeout), MAX_WAIT_POLL_S))
        if since is None:
            since = pending.get(device_id, time.time())
        # The computer may have changed Wi-Fi since the server started; devices
        # are told to send data to this address, so keep it current.
        ip = await asyncio.to_thread(default_lan_ip)
        if (ip and not ip.startswith("127.") and ip != state.discovery.pc_host
                and ipaddress.ip_address(ip) not in SETUP_NET):
            state.discovery.pc_host = ip
        t0 = time.monotonic()
        try:
            dev = await asyncio.to_thread(pclient.wait_for_device, device_id, timeout,
                                          state.discovery, 0.25, since)
        except ProvisionError:
            return {"found": False, "waited_s": round(time.monotonic() - t0, 1)}
        return {"found": True, "device": dev,
                "waited_s": round(time.monotonic() - t0, 1)}

    @app.get("/api/setup/wifi-hint")
    async def setup_wifi_hint(request: Request):
        require_local(request)
        ssid = await asyncio.to_thread(wifi_hint)
        return {"ssid": ssid}

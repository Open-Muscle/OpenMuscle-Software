# om_provisioning.py
# Device-side AP/SoftAP provisioning per PROVISIONING.md v1.0.
#
# Canonical / device-agnostic. Mirrors FlexGridV4-Firmware/lib/provisioning.py
# with the embedded-lib logging convention (om_logger). LASK5-Firmware
# vendors this file directly; future promoted device firmware can adopt it
# unchanged.
#
# Boot path (the caller wires the fork):
#   unprovisioned (no wifi_ssid)  ->  om_provisioning.run(...)
#                                     starts WPA2-PSK AP "OM-<dev>-<id-tail>",
#                                     binds HTTP server on 192.168.4.1:80,
#                                     blocks until POST /provision lands and
#                                     persists creds, then soft-resets.
#   provisioned                   ->  normal device startup (STA mode).
#                                     Optionally spawn om_provisioning.serve
#                                     bound to the STA IP for STA-mode
#                                     /info + /reprovision (PROVISIONING.md 4.3).
#
# Endpoints (PROVISIONING.md section 4):
#   GET  /info           -> device id, dev type, fw, caps, state, [extras]
#   POST /provision      -> {ssid, password, hub_host?, hub_port?} ->
#                          persist + ack + soft-reset
#   POST /reprovision    -> clear ssid + rotate PSK + soft-reset
#   GET  /               -> minimal HTML captive-portal page
#
# Security model: WPA2-PSK with a per-device RANDOM PSK delivered OUT OF BAND
# via the OLED. Never derived from any public field, never transmitted in the
# clear over the radio. See PROVISIONING.md section 3.

import os
import socket
import ujson
import time
import network
import uasyncio as asyncio
import machine
import om_logger as log


# Charset excludes the ambiguous pairs 0/o and 1/l (so OLED reading + phone
# typing stays clean). 32 chars, 5 bits/char.
PSK_CHARSET = "abcdefghijkmnpqrstuvwxyz23456789"
PSK_LENGTH  = 10

# AP parameters per PROVISIONING.md section 3.
AP_IP            = "192.168.4.1"
AP_NETMASK       = "255.255.255.0"
AP_GATEWAY       = "192.168.4.1"
AP_DNS           = "192.168.4.1"
AP_CHANNEL       = 1
AP_MAX_CLIENTS   = 4
AP_AUTH_WPA2_PSK = 3   # network.AUTH_WPA2_PSK == 3 on ESP32 MicroPython

HTTP_PORT       = 80
PROVISION_REBOOT_DELAY_MS = 1500


def mint_psk():
    """Generate a 10-char random PSK from os.urandom. ~50 bits of entropy.
    Excludes ambiguous pairs (0/o, 1/l) so OLED -> phone typing is clean."""
    raw = os.urandom(PSK_LENGTH)
    return "".join(PSK_CHARSET[b & 0x1F] for b in raw)


def ssid_for(device_type, device_id):
    """SSID = OM-<dev>-<id-tail>. id-tail is device_id with the '<dev>-'
    type prefix stripped (e.g. lask5-01 -> 01)."""
    prefix = device_type + "-"
    tail = device_id[len(prefix):] if device_id.startswith(prefix) else device_id
    return "OM-{}-{}".format(device_type, tail)


# ---------------------------------------------------------------------------
# HTTP/1.1 request + response helpers. MicroPython has no built-in HTTP
# server, so we parse one request per connection (no keep-alive) directly.
# Bodies are JSON unless the route returns HTML.
# ---------------------------------------------------------------------------

def _parse_request(raw):
    """Returns (method, path, headers_dict, body_bytes). Tolerates LF or
    CRLF; rejects malformed requests by raising ValueError."""
    head_end = raw.find(b"\r\n\r\n")
    if head_end < 0:
        head_end = raw.find(b"\n\n")
        if head_end < 0:
            raise ValueError("incomplete request head")
        sep_len = 2
    else:
        sep_len = 4
    head = raw[:head_end].decode("utf-8", "replace")
    body = raw[head_end + sep_len:]
    lines = head.split("\n")
    request_line = lines[0].strip()
    parts = request_line.split(" ")
    if len(parts) < 2:
        raise ValueError("bad request line")
    method = parts[0].upper()
    path = parts[1]
    headers = {}
    for ln in lines[1:]:
        ln = ln.strip()
        if not ln or ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return method, path, headers, body


def _respond(writer, status_code, status_text, body, content_type="application/json"):
    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    writer.write("HTTP/1.1 {} {}\r\n".format(status_code, status_text).encode("utf-8"))
    writer.write("Content-Type: {}\r\n".format(content_type).encode("utf-8"))
    writer.write("Content-Length: {}\r\n".format(len(body_bytes)).encode("utf-8"))
    writer.write(b"Cache-Control: no-store\r\n")
    writer.write(b"Connection: close\r\n")
    writer.write(b"\r\n")
    writer.write(body_bytes)


def _json_body(headers, body):
    if not body:
        return {}
    try:
        return ujson.loads(body.decode("utf-8"))
    except Exception as e:
        raise ValueError("invalid_json: {}".format(e))


# ---------------------------------------------------------------------------
# Captive-portal HTML (minimal; PROVISIONING.md section 4.4). One inline
# script, plain CSS, no external assets so it works on every phone browser.
# ---------------------------------------------------------------------------

_CAPTIVE_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenMuscle Setup</title>
<style>body{font-family:system-ui,sans-serif;max-width:24rem;margin:2rem auto;padding:0 1rem;color:#222}
h1{font-size:1.3rem;margin:0 0 .25rem}p{color:#555;margin:.25rem 0 1rem}
label{display:block;margin:.75rem 0 .25rem;font-weight:600}
input{width:100%;box-sizing:border-box;padding:.6rem;font-size:1rem;border:1px solid #ccc;border-radius:.4rem}
button{margin-top:1rem;width:100%;padding:.75rem;font-size:1rem;background:#1a73e8;color:#fff;border:0;border-radius:.4rem}
#msg{margin-top:1rem;font-size:.95rem}</style>
</head><body><h1>OpenMuscle: connect to Wi-Fi</h1>
<p id="dev">Loading device info...</p>
<form id="f"><label>Wi-Fi name (SSID)</label><input name="ssid" required autocomplete="off">
<label>Wi-Fi password</label><input name="password" type="password">
<button type="submit">Connect</button></form><div id="msg"></div>
<script>
fetch("/info").then(r=>r.json()).then(d=>{document.getElementById("dev").textContent="Device "+d.id+" ("+d.dev+", "+d.fw+")"});
document.getElementById("f").addEventListener("submit",e=>{e.preventDefault();
const f=new FormData(e.target),m=document.getElementById("msg");m.textContent="Connecting...";
fetch("/provision",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({ssid:f.get("ssid"),password:f.get("password")})})
.then(r=>r.json()).then(d=>{m.textContent=d.status=="ok"?"Saved. Device is rebooting; reconnect to your home Wi-Fi.":("Error: "+(d.data&&d.data.message||"unknown"))})
.catch(e=>m.textContent="Error: "+e)});
</script></body></html>"""


# ---------------------------------------------------------------------------
# State carried by the HTTP handlers.
# save_fn is a no-arg callable that persists state.settings (the caller
# adapts to whatever settings module it uses; e.g. V4's SettingsManager.save
# takes a dict, LASK5's Settings instance has its own .save()).
# ---------------------------------------------------------------------------

class _ProvisioningState:
    def __init__(self, settings, save_fn, info_extras, state="unprovisioned"):
        self.settings = settings
        self.save_fn = save_fn
        self.info_extras = info_extras or {}
        self.reboot_pending = False
        self.state = state   # "unprovisioned" (AP mode) or "provisioned" (STA mode)

    def info_payload(self):
        d = {
            "v":     "1.0",
            "type":  "info",
            "id":    self.settings.get("device_id"),
            "dev":   self.settings.get("device_type"),
            "fw":    self.settings.get("fw_version"),
            "caps":  self.info_extras.get("caps", []),
            "state": self.state,
        }
        # Optional device-type-specific fields (e.g. matrix dims for FlexGrid,
        # piston count for LASK5).
        for k, v in self.info_extras.items():
            if k == "caps":
                continue
            d[k] = v
        return d


async def _handle(reader, writer, state):
    try:
        raw = await reader.read(2048)
        if not raw:
            return
        try:
            method, path, headers, body = _parse_request(raw)
        except ValueError as e:
            _respond(writer, 400, "Bad Request",
                     ujson.dumps({"status": "error", "data": {"message": str(e)}}))
            await writer.drain()
            return

        if method == "GET" and path == "/info":
            _respond(writer, 200, "OK", ujson.dumps(state.info_payload()))

        elif method == "POST" and path == "/provision":
            try:
                req = _json_body(headers, body)
            except ValueError as e:
                _respond(writer, 400, "Bad Request",
                         ujson.dumps({"v": "1.0", "type": "provision_ack",
                                      "status": "error",
                                      "data": {"message": str(e)}}))
                await writer.drain()
                return
            ssid = (req.get("ssid") or "").strip()
            password = req.get("password") or ""
            hub_host = req.get("hub_host")
            hub_port = req.get("hub_port")
            if not (1 <= len(ssid) <= 32):
                _respond(writer, 400, "Bad Request",
                         ujson.dumps({"v": "1.0", "type": "provision_ack",
                                      "status": "error",
                                      "data": {"message": "ssid_length_invalid (1..32)"}}))
                await writer.drain()
                return
            if password and not (8 <= len(password) <= 63):
                _respond(writer, 400, "Bad Request",
                         ujson.dumps({"v": "1.0", "type": "provision_ack",
                                      "status": "error",
                                      "data": {"message": "password_length_invalid (8..63 for WPA2, or empty)"}}))
                await writer.drain()
                return
            # Persist creds. Do NOT verify the join here; if the user typed
            # the password wrong, STA fails to join and the device falls
            # back to unprovisioned on next boot (PROVISIONING.md section 2).
            state.settings["wifi_ssid"] = ssid
            state.settings["wifi_password"] = password
            if hub_host:
                state.settings["hub_host"] = hub_host
            if hub_port:
                state.settings["hub_port"] = int(hub_port)
            state.save_fn()
            log.info("Provisioned: ssid='{}' (saved); rebooting in {}ms".format(
                ssid, PROVISION_REBOOT_DELAY_MS))
            _respond(writer, 200, "OK",
                     ujson.dumps({"v": "1.0", "type": "provision_ack",
                                  "status": "ok",
                                  "id":     state.settings.get("device_id"),
                                  "next":   "switching_to_sta",
                                  "reboot_in_ms": PROVISION_REBOOT_DELAY_MS}))
            state.reboot_pending = True

        elif method == "POST" and path == "/reprovision":
            # Clear creds + rotate PSK so a returned-to-factory device does
            # not present the same PSK to the next owner.
            state.settings["wifi_ssid"] = ""
            state.settings["wifi_password"] = ""
            state.settings["provisioning_psk"] = mint_psk()
            state.save_fn()
            log.info("Reprovision requested; PSK rotated; rebooting in {}ms".format(
                PROVISION_REBOOT_DELAY_MS))
            _respond(writer, 200, "OK",
                     ujson.dumps({"v": "1.0", "type": "reprovision_ack",
                                  "status": "ok",
                                  "reboot_in_ms": PROVISION_REBOOT_DELAY_MS}))
            state.reboot_pending = True

        elif method == "GET" and path == "/":
            _respond(writer, 200, "OK", _CAPTIVE_HTML, content_type="text/html; charset=utf-8")

        else:
            _respond(writer, 404, "Not Found",
                     ujson.dumps({"status": "error",
                                  "data": {"message": "no_route", "method": method, "path": path}}))

        await writer.drain()
    except Exception as e:
        log.warn("Provisioning HTTP handler failed: {}".format(e))
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def start_ap(settings, save_fn, device_type, device_id):
    """Bring up the WPA2-PSK access point. Returns the configured PSK so the
    caller can show it on the OLED. Mints + persists a new PSK if the
    settings dict does not already have one."""
    psk = settings.get("provisioning_psk")
    if not psk or len(psk) != PSK_LENGTH:
        psk = mint_psk()
        settings["provisioning_psk"] = psk
        save_fn()
        log.info("Minted new provisioning PSK; persisted")

    ssid = ssid_for(device_type, device_id)

    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    # ifconfig before config so the address space is set when clients DHCP.
    try:
        ap.ifconfig((AP_IP, AP_NETMASK, AP_GATEWAY, AP_DNS))
    except Exception as e:
        log.warn("AP ifconfig failed (continuing): {}".format(e))
    try:
        ap.config(essid=ssid, password=psk, authmode=AP_AUTH_WPA2_PSK,
                  channel=AP_CHANNEL, max_clients=AP_MAX_CLIENTS, hidden=False)
    except Exception as e:
        # Older MicroPython builds may not accept all kwargs; retry with the
        # minimal set that every ESP32 port supports.
        log.warn("AP config full kwargs failed ({}); retrying minimal".format(e))
        ap.config(essid=ssid, password=psk, authmode=AP_AUTH_WPA2_PSK)

    log.info("AP up: ssid='{}' psk='{}' ip={}".format(ssid, psk, AP_IP))
    return psk


async def serve(state, bind_ip=AP_IP):
    """Run the HTTP server. In AP mode (bind_ip=AP_IP, the default) this
    blocks until state.reboot_pending and then soft_resets, which is the
    boot-time provisioning flow. In STA mode (bind_ip=device's LAN IP)
    this runs forever as a background task, serving /info and /reprovision
    against the home LAN per PROVISIONING.md section 4.3. Reprovision
    still soft_resets when triggered (the user is intentionally rebooting
    the device back to AP mode).

    Bind to a specific IP rather than 0.0.0.0 so we do not accidentally
    expose the reprovision endpoint on both interfaces simultaneously."""
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, state), bind_ip, HTTP_PORT)
    log.info("Provisioning HTTP server listening on {}:{} (state={})".format(
        bind_ip, HTTP_PORT, state.state))

    try:
        while not state.reboot_pending:
            await asyncio.sleep(0.2)
    finally:
        try:
            server.close()
        except Exception:
            pass

    # Give the OS time to flush the HTTP response before the radio drops.
    await asyncio.sleep_ms(PROVISION_REBOOT_DELAY_MS)
    log.info("Provisioning complete; soft_reset()")
    machine.soft_reset()


async def run(settings, save_fn, device_type, device_id, info_extras=None,
              status_led_blue_blink=None):
    """Entry point. Boot to AP mode, serve provisioning endpoints, soft-reset
    when the user finishes. Returns only on errors (callers should treat
    a return as a hard fault and fall back to STA-retry behavior).

    settings:           live settings dict-like (mutated; persisted by save_fn).
    save_fn:            no-arg callable that persists `settings`. Adapt per
                        device (e.g. lambda: SettingsManager.save(settings),
                        or lambda: settings.save() for the LASK5 Settings
                        instance shape).
    device_type:        e.g. "flexgrid", "lask5".
    device_id:          e.g. "lask5-01".
    info_extras:        dict merged into GET /info (e.g. {"caps": [...],
                        "pistons": 4, "joystick": True}).
    status_led_blue_blink: optional callable; awaited in the background if given.
    """
    psk = start_ap(settings, save_fn, device_type, device_id)

    state = _ProvisioningState(settings, save_fn, info_extras)
    tasks = [asyncio.create_task(serve(state))]
    if status_led_blue_blink is not None:
        tasks.append(asyncio.create_task(status_led_blue_blink()))

    for t in tasks:
        await t

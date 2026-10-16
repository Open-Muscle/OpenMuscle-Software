# USB console: the band's command channel on the USB cable

The band answers a few JSON commands on its USB serial port. The PC app uses them to give a band its Wi-Fi over the cable, so a new owner never has to join the band's own setup network. This page is the exact protocol, for people writing the other end (the app, a script, a test rig). For the plain-words version see [Setting up over the USB cable](device-reference.md#setting-up-over-the-usb-cable).

Code: `lib/usb_console.py`. Tests: `tests/test_usb_console.py` (which also runs every example on this page against the code).

## Why it exists

Setup used to mean joining the band's own Wi-Fi network (`OM-flexgrid-...`) and opening `http://192.168.4.1`. That needs the band's radio to act as an access point, which a laptop does not always reach and which the band cannot start at all on battery. The USB cable is already there during setup, so the Wi-Fi details go down the cable instead.

The setup network and the `192.168.4.1` page still work exactly as before. They are the fallback, for a computer without the app or a band whose cable port will not open.

## Transport

| | |
|---|---|
| Port | the band's USB serial port (MicroPython USB CDC, USB id `303A:4001`) |
| Speed | 115200 baud, 8N1 (the speed is nominal on USB, but set it) |
| Control lines | keep DTR asserted; never pulse RTS (pulsing RTS can reset the chip) |
| Framing | one JSON object per line, newline (`\n`) terminated, UTF-8 |
| Replies | exactly one JSON line per command |

The band also prints its normal log lines on this port (`[INF] ...`). The host reads line by line and keeps only lines that start with `{`.

The band ignores, without any reply: blank lines, lines that do not start with `{`, and lines that contain control bytes (the raw-REPL traffic of `mpremote` and `flash.py`). A command line should stay under about 700 bytes; longer lines are dropped. A half-received line older than about 2 seconds is thrown away when the next byte arrives, so send a bare newline first if the previous attempt may have been cut off.

The console runs in both modes: in setup mode (no Wi-Fi saved) and on a band that has Wi-Fi.

## Commands

Every command has this shape (`id` is any short string; the reply repeats it):

```text
{"v": "1.0", "type": "cmd", "id": "q1", "data": {"verb": "get_info"}}
```

| Verb | Fields in `data` | What it does |
|---|---|---|
| `get_info` | none | Report the band's identity and Wi-Fi state |
| `set_wifi` | `ssid`, `password` | Save the Wi-Fi details, reply, then restart the band |
| `forget_wifi` | none | Erase the saved Wi-Fi details, reply, then restart into setup mode |
| `set_role` | `role`: `left`, `right`, `labeler` or `""` | Set which side the band is worn on (same as the network `set_role` command). No restart |

### Replies

Success:

```text
{"v": "1.0", "type": "ack", "id": "q1", "ok": true, "data": {}}
```

Failure:

```text
{"v": "1.0", "type": "ack", "id": "q1", "ok": false, "error": "<code>", "message": "<plain words>"}
```

Error codes:

| Code | Meaning |
|---|---|
| `bad_json` | The line starts with `{` but is not a valid JSON object (or `data` is not an object). The `id` in the reply is empty |
| `unknown_verb` | `data.verb` is missing or not one of the four verbs |
| `bad_ssid` | `ssid` is missing, blank, or not 1 to 32 bytes of UTF-8 |
| `bad_password` | `password` is not 8 to 63 bytes of UTF-8 (or empty for an open network) |
| `save_failed` | The band could not write its settings to flash. Nothing changed and the band did not restart. Also the fallback for an unexpected failure inside a verb; the message then ends with the error type (for example `OSError`) |

Lengths are in UTF-8 bytes, as Wi-Fi counts them, not in characters. The name and password are stored exactly as sent (no trimming). A missing `password`, or `null`, means an open network.

A line whose `type` is something other than `cmd` (for example an `ack` echoed back by a terminal) is ignored, never answered.

## Examples

Every example below is checked by the tests against the code. The band here is joined to a network called `HomeNet` at `192.168.1.50`, has the id `flexgrid-abc123` and the role `left`.

### get_info

```json
{"v": "1.0", "type": "cmd", "id": "q1", "data": {"verb": "get_info"}}
```

```json
{"v": "1.0", "type": "ack", "id": "q1", "ok": true, "data": {"id": "flexgrid-abc123", "role": "left", "fw": "v4.0.0", "state": "provisioned", "wifi_ssid": "HomeNet", "ip": "192.168.1.50", "usb_console": 1}}
```

| Field | Meaning |
|---|---|
| `id` | the band's id (the same id as in its announcements) |
| `role` | `left`, `right`, `labeler`, or `""` |
| `fw` | firmware version |
| `state` | `unprovisioned` (no Wi-Fi saved: the band is in setup mode) or `provisioned` (a Wi-Fi name is saved; this does not prove a join has worked yet) |
| `wifi_ssid` | the saved Wi-Fi name, or `""` |
| `ip` | the band's current address on that Wi-Fi, or `""` when it is not connected (always `""` in setup mode) |
| `usb_console` | always `1`; tells the host this firmware has the console |

The Wi-Fi password is never reported.

### set_wifi

```json
{"v": "1.0", "type": "cmd", "id": "w1", "data": {"verb": "set_wifi", "ssid": "HomeNet", "password": "correct horse"}}
```

```json
{"v": "1.0", "type": "ack", "id": "w1", "ok": true, "data": {"restart": true}}
```

On success the band has already written the details (and set the "joined at least once" flag back to false, cleared the last-failure fields, and kept its id, role and setup password). It sends the reply, waits about one second so the reply gets out, then resets itself. A reset on this chip drops the USB port and brings it back, often under a different port name on Windows, so the host should close the port after the reply and look for the band again.

After the restart the band tries to join the network (blue steady light for up to 20 seconds). If the first join fails (wrong password, 5 GHz only) the band forgets the details and returns to setup mode, as it does for the web page; `get_info` then shows `state: unprovisioned`. The reply to `set_wifi` only says the details were saved: it does not say the join worked. To check, wait for the band to come back and read `get_info` (`ip` filled in), or watch for its announcement on the network.

An error example:

```json
{"v": "1.0", "type": "cmd", "id": "w2", "data": {"verb": "set_wifi", "ssid": "HomeNet", "password": "short"}}
```

```json
{"v": "1.0", "type": "ack", "id": "w2", "ok": false, "error": "bad_password", "message": "The Wi-Fi password must be 8 to 63 characters, or empty for an open network."}
```

An open network:

```json
{"v": "1.0", "type": "cmd", "id": "w3", "data": {"verb": "set_wifi", "ssid": "Guest cafe", "password": ""}}
```

```json
{"v": "1.0", "type": "ack", "id": "w3", "ok": true, "data": {"restart": true}}
```

### forget_wifi

```json
{"v": "1.0", "type": "cmd", "id": "f1", "data": {"verb": "forget_wifi"}}
```

```json
{"v": "1.0", "type": "ack", "id": "f1", "ok": true, "data": {"restart": true}}
```

The saved name and password are erased, the band replies, and restarts into setup mode. Its setup network name and password (the ones on the sticker) are kept.

### set_role

```json
{"v": "1.0", "type": "cmd", "id": "r1", "data": {"verb": "set_role", "role": "right"}}
```

```json
{"v": "1.0", "type": "ack", "id": "r1", "ok": true, "data": {"role": "right"}}
```

The band reports the role it stored. A value that is not `left`, `right` or `labeler` (including a missing `role`) stores `""`, the same as the network command. The role is saved and the light colour changes at once; there is no restart.

### Unknown verb

```json
{"v": "1.0", "type": "cmd", "id": "x1", "data": {"verb": "reboot"}}
```

```json
{"v": "1.0", "type": "ack", "id": "x1", "ok": false, "error": "unknown_verb", "message": "Unknown verb: reboot"}
```

## Trying it by hand

With `pyserial` installed (`pip install pyserial`), replace `COM8` with the band's port:

```python
import json, serial

port = serial.Serial("COM8", 115200, timeout=2)
port.dtr = True
port.write(b"\n")                      # resync: ends any half-sent line
cmd = {"v": "1.0", "type": "cmd", "id": "t1", "data": {"verb": "get_info"}}
port.write(json.dumps(cmd).encode() + b"\n")
while True:
    line = port.readline().decode("utf-8", "replace").strip()
    if not line:
        break                          # timed out: nothing more to read
    if line.startswith("{"):
        print(json.loads(line))
        break
```

When a band that already has a Wi-Fi saved is reset, the console only starts once its join attempt has finished (up to about 20 seconds). A host that sends a command right after a reset should retry `get_info` for about 30 seconds before treating silence as a failure.

Do not run this at the same time as `mpremote`, `flash.py` or `tools/kit_check.py` on the same port: only one program can hold a serial port.

## Safety notes

- The band never writes the Wi-Fi name or password to its log, and `get_info` never returns the password. Hosts should not log them either.
- Anyone who can plug a cable into the band can set its Wi-Fi. That is the same trust as holding the band, and is why the USB route needs no setup password.
- Ctrl-C from `mpremote` and `flash.py` still stops the program and opens the REPL as before; the console never takes Ctrl-C.

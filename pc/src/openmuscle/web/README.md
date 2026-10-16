# `openmuscle web`: developer notes

The Studio is a single-page FastAPI app (vanilla JS, no build step) for live monitoring, recording, training and prediction against OpenMuscle devices on the local network. It uses the same UDP listener as the `openmuscle receive` matplotlib viewer, rendered in a browser instead.

This page is for people changing the code. To **use** the Studio, read [getting-started.md](../../../../docs/getting-started.md), [studio-tour.md](../../../../docs/studio-tour.md) (every panel and button), [your-data.md](../../../../docs/your-data.md) and [troubleshooting.md](../../../../docs/troubleshooting.md). Command-line options are in [pc-cli.md](../../../../docs/pc-cli.md).

## Run it

```
pip install -e .             # from pc/, if not already (uses fastapi, uvicorn, pydantic)
openmuscle web               # http://localhost:8000 (this computer only), UDP :3141
# your browser opens by itself; leave the window open, Ctrl+C stops it
```

Everything you record is saved under `~/OpenMuscle` (`captures/`, `sessions/`, `models/`, `exports/`, `logs/`), whichever folder you start from. The banner and `GET /api/storage` show the exact absolute path. Plain `openmuscle web` is `http` and binds `127.0.0.1`; `--lan`, `--vr`, `--debug` and `--ssl-*` open the network (see [pc-cli.md](../../../../docs/pc-cli.md) for every flag).

Developer-only options:
```
openmuscle web --port 8000 --udp-port 3141 --captures-dir /some/folder
openmuscle web --model ~/OpenMuscle/models/<model folder>/model.pkl   # live ML inference
openmuscle web --model X --hand 10.0.0.55                      # also forward to robot hand
openmuscle web --model X --hand 10.0.0.55:3145                 # custom hand port
```

For a second instance during development use other ports (`--port 8020 --udp-port 53200 --announce-port 53201`) so you do not fight a running one over the bands.

**Firewall.** The bands send UDP to this computer (3141 for data, 3140 for discovery beacons). The per-OS recipes, including `openmuscle firewall` on Windows, are in [troubleshooting.md](../../../../docs/troubleshooting.md#firewall-windows-mac-linux). `openmuscle doctor` checks all of this and names the fix; the app's "why can't I see my device?" panel uses the same checks (`GET /api/discovery/diagnose`).

**Keeping the computer awake.** While the server runs it asks the operating system not to sleep from inactivity (Windows `SetThreadExecutionState`, macOS `caffeinate`; nothing on Linux), so a recording is not cut off. Closing a laptop lid still puts it to sleep. `--no-keep-awake` turns this off.

**Ports.** A second `openmuscle web` is detected and exits with one line. The UDP data port is opened exclusively (no address reuse), so a program that already holds it is reported rather than silently sharing it; the UI shows a bound/not-bound state for both UDP ports in `GET /api/discovery/status`.

## Setting up a device (`/setup`)

The **Set up a device** link in the top bar (and the empty state on the live view) opens a step-by-step page at `http://localhost:8000/setup` that puts a band, or the optional finger labeler, on your Wi-Fi. The user-facing walkthrough is [wifi-setup.md](../../../../docs/wifi-setup.md); the steps in code are:

1. **Get ready:** a 2.4 GHz Wi-Fi, its name and password, no sign-in page (phone hotspot help included).
2. **Choose** band or labeler. A band goes to the **Plug the band into this computer** step (the USB road, below); the labeler goes to Join.

   **The USB road (a band, the default).** The page polls `GET /api/setup/usb/ports` while the step is open, shows the band it finds (id and role; a chooser when several), asks for the Wi-Fi name (prefilled from `wifi-hint`) and password, and posts them to `POST /api/setup/usb/provision`. The band restarts and the page hands over to the Finish step with the band's id, so the green tick comes from the band announcing on the LAN. The computer never leaves its Wi-Fi. The button **Use the band's own Wi-Fi instead** leads to steps 3 to 5 below, for people without a data cable. The host side of the USB line protocol is `provision/usb.py`; `openmuscle setup-usb` is the same thing on the command line.
3. **Join** the device's own `OM-...` network from this computer's Wi-Fi menu. The page notices when it is connected, shows the device id, and asks you to check it against the end of the name on the sticker (or the code on the device screen, if it has one).
4. **Your Wi-Fi:** pick the name from the list the device sees (or type it), enter the password (Show/Hide), and send. "Remember for the next device" keeps the details in this browser tab only (`sessionStorage`; never `localStorage`, never on disk), so the second band needs no typing.
5. **Finish:** reconnect this computer to your usual Wi-Fi; a checklist follows the restart and ends with "Band ... is online at <address>". After 60 seconds without the device it shows an ordered troubleshooting list (wrong password, 5 GHz or sign-in network, different network, firewall, router client isolation, add by address).

The page only talks to this program, which relays to the device (a browser on the device's network cannot reach your home network at the same time). The relay routes are loopback only: any caller that is not this computer, even with `--lan` or `--debug`, gets 403, as does a browser Origin that is not `localhost`/`127.0.0.1`. The device address is limited to `192.168.4.0/24` (the device's own network). The Wi-Fi name and password travel only in the request body and are scrubbed from the log panel and the access log.

| Route | What it does |
|-------|--------------|
| `GET /api/setup/probe[?host=]` | Is a device in setup mode answering? `{reachable, info:{id,dev,fw,state,last_wifi_error,ap_name}}` or `{reachable:false, reason, message}` |
| `GET /api/setup/scan[?host=]` | Networks the device can see: `{supported, networks:[{ssid,rssi,auth}]}` |
| `POST /api/setup/provision` | `{ssid, password, host?, expect_id?}` to `{ok:true, id, reboot_in_ms, wait_s, since}` or `{ok:false, code, message}` (409 `wrong_device` when `expect_id` does not match) |
| `POST /api/setup/reprovision` | `{device_id}`: put an online device (address taken from discovery) back in setup mode |
| `GET /api/setup/wait?device_id=&timeout=&since=` | Long-poll (max 30 s) for the device to show up on your network: `{found, device?, waited_s}` |
| `GET /api/setup/wifi-hint` | `{ssid}`: the Wi-Fi this computer is on (`netsh`, `nmcli` or `networksetup`), or `null` |
| `GET /api/setup/usb/ports` | Bands plugged in with a USB cable: `{ports:[{port,description,answered,id,role,state,fw,wifi_ssid}]}`. A port with `answered:false` is plugged in but silent. Each answering band is asked `get_info` on its serial port; ports are found by the band's USB ids and nothing else is opened |
| `POST /api/setup/usb/provision` | `{port, ssid, password, expect_id?}` to `{ok:true, id, role, reboot_in_ms, wait_s, since}` or `{ok:false, code, message}`. Codes: `no_port` (404), `many_ports`, `no_answer` (504, replug the band), `rejected` (422, with the band's own `band_code`), `wrong_device` (409), plus the same `ssid_length_invalid` / `password_length_invalid` as above. `port` must be one the operating system lists as a band. `since` is a moment after the band saves, so a beacon sent just before it restarts does not count as it joining |

The troubleshooting list calls `GET /api/discovery/diagnose` when this version has it and shows a plain message when it does not.

## Debug mode (`--debug`)

`openmuscle web --debug` serves plain HTTP on all interfaces (drops any mkcert cert, even with `--vr`) so the PC, phone, and Quest all connect with zero cert friction, and unlocks the extra troubleshooting surface. The frontend reads `GET /api/mode` on load; when debug is on it shows a red "LAN-open + unauthenticated" ribbon plus:

- **Debug panel** (per device): stream health (Hz, live/stale, packets, subscribe state), per-channel matrix stats (active/total, max, mean), IMU raw counts + the per-chip scale flag, forearm roll/palm-up for Quest devices, and a freeze-able raw-frame inspector (the selected device's live snapshot as JSON).
- **Both flexgrid heatmaps at once**, side-by-side or stacked (a per-user toggle), so a two-hand session shows both forearms live.
- **Live capture-quality verdict** (GOOD / DEGRADED / BAD) during recording, from match-rate + joint-drop count, so a bad take is caught in real time.

The server is LAN-open and unauthenticated in this mode (`/api/reveal` is disabled), so it is for the lab bench / demos, not the internet. WebXR hand-tracking still needs a secure context, so plain-HTTP `/vr` is view-only unless you tether the Quest (`adb reverse tcp:8000 tcp:8000` → `http://localhost:8000/vr`).

## Labeled-recording sessions

Start a session (Stage 2) to make each recording self-describing: `session.json` snapshots the connected-device roster (id, role, matrix shape, Hz, firmware, IMU scale, battery) plus the participant code, take number, arm (left, right or both), labeler source (webcam / LASK5 / VR / both), a video reference, and a `started_at_ms` sync marker on the hub clock (so a screen or headset recording pairs to the exact CSV rows). The session form shows four fields (name, participant code, arm, where the finger data comes from) and a collapsed More details section for the rest. It prefills code / arm / labeler from the last session and bumps the take number. The participant code is a code such as P01, never a name: it is stored under both `wearer` and `subject` in `session.json` (the model-mismatch guard reads `wearer`, capture meta uses `subject`), a value with a space gets a soft warning but still saves, and the Edit button on a session card reopens the same form to change any of it (`PUT /api/sessions/{id}`). Captures made in a session inherit its context, including the code, in their `.meta.json`.

Short takes are never hidden or deleted. A take under 10 s (or 50 rows) is tagged "too short" in the captures table with a tooltip that says to record at least 10 s and that 30 s or more works best, the post-stop card says the same, and Go to Train does not tick it. The numbers live in one module, `openmuscle/take_limits.py`, read by the recorder, the table, the post-stop card and the trainer (`short_captures` in the train result). While recording, the status shows a big timer, one plain sentence per problem (wording in `static/rec-status.js`), and the technical counters only under Details.

## What is on the page

The user-facing description of each panel is [studio-tour.md](../../../../docs/studio-tour.md). Where they live in the source:

| Area in `static/index.html` | Source of its data | Notes |
|---|---|---|
| Control bar (Record, Finger data from, Predict, session chip, SENSOR/LABEL pills) | `/ws/live` snapshot, `/api/recording`, `/api/inference/*` | `static/guide.js` decides what Record will do and why it is blocked; `static/rec-status.js` turns the quality verdict into words |
| Stage 1, Live: Camera panel, band rows, heatmaps | `snapshot.devices`, `snapshot.discovery` | One row per streaming FlexGrid: band card left, heatmap right. Heatmap colours come from `static/heatmap-color.js` |
| Ground truth vs Predicted | `snapshot.devices` (label devices) and `snapshot.inference` | Piston bars for the LASK5, a 3D hand (`static/hand-viewer.js`) for camera and headset hands |
| Stage 2, Data and Models: storage, session, captures, train, models | `/api/storage`, `/api/sessions`, `/api/captures`, `/api/train`, `/api/models` | Train result cards: `static/train-card.js` |
| Diagnostics and logs, Output routing | `/api/logs`, `/api/inference/hand` | The Debug panel appears only in `--debug` |
| `/setup` | `/api/setup/*` | See the section above |

## Architecture

```
pc/src/openmuscle/web/
├── app.py            FastAPI app: routes, lifespan, WebSocket endpoints, no-cache middleware
├── state.py          AppState: owns the UDPListener, the discovery manager, the device
│                     registry, recording, sessions, WebSocket clients and the broadcaster
│                     task. One shared instance per process.
├── setup_api.py      The loopback-only /api/setup/* routes (relay to a band's setup server)
├── inference.py      InferenceEngine: loads model.pkl and predicts from live frames
├── log_buffer.py     In-memory log ring buffer behind GET /api/logs
└── static/
    ├── index.html    Single-page UI skeleton
    ├── app.js        WebSocket client, heatmaps, recording controls, tables, training UI
    ├── guide.js, rec-status.js, train-card.js, participant.js, heatmap-color.js
    │                 Pure functions (unit tested under Node in pc/tests/js)
    ├── camera-panel.js, gamepad.js, hand-viewer.js, imu-viewer.js
    ├── setup/        The device setup wizard page (wizard.js holds the wording and logic)
    ├── webcam/       The standalone camera hand-tracking page (/webcam)
    ├── vr/           The WebXR client (/vr)
    └── styles.css, camera-panel.css
```

**Data flow** for one packet:

```
device --UDP--> UDPListener.thread --queue--> AppState.run_broadcaster (async)
                                                    │
                                          DeviceInfo.update(pkt)
                                                    │
                                          ┌─────────┴────────┐
                                          ↓                  ↓
                              if recording: writer       broadcast snapshot
                              .write_row(...)            to all WS clients
```

The `_snapshot()` dict pushed to every connected browser is the contract (abridged):

```json
{
  "type": "tick",
  "devices": [{"device_id":..., "device_type":..., "hz":...,
               "matrix": [[...]],          // flexgrid
               "values": [...],            // lask5 pistons / 1-D payloads
               "joystick": {"x":..,"y":..} // lask5 only
              }, ...],
  "discovery": [...],                      // known V4 sources: ip, role, subscribed, ...
  "recording": null,
  "inference": {"available": false, "model": null, "piston_values": null,
                "status": "no model loaded"}
}
```

## Where are my files, and sending them back

The Data stage starts with the absolute folders recordings and models are saved
in, each with **Copy path** and, when you are on the computer running
OpenMuscle, **Open folder**. A warning appears when the drive has less than 1 GB
free. Open folder is hidden for other devices on the network and under
`--debug`, and the server refuses the call from them (403).

To hand a session to someone, use **Export session** on the session card (or
tick takes and use **Export selected**). The dialog shows the estimated size and
saves one zip through the browser, which puts it in its download location
(usually Downloads); a copy is also kept in `~/OpenMuscle/exports`. The same
zip from the command line:

```
openmuscle bundle <session id or name>                  # -> ~/OpenMuscle/exports/openmuscle-session-<id>.zip
openmuscle bundle "morning baseline" -o baseline.zip
openmuscle bundle --take a.csv --take b.csv             # loose takes, no session
openmuscle bundle <session> --raw --notes               # opt in to raw streams and notes
```

The zip holds `README.txt` (every file explained in plain words, units, how the
quality verdict works), `session.json`, `summary.csv` (one row per take) and, in
`takes/`, each take's CSV and `.meta.json` (plus `.labels.schema.json` when there
is one). Compact by default: the raw `.sensor.jsonl` / `.label.jsonl` streams,
your typed notes and trained models are left out (models are never included).
Session names and, if you opt in, notes are free text: keep them free of names
and use a participant code. Over about 20 MB many email services refuse the file;
leave raw streams off or export one session at a time. The existing
`openmuscle export` command (model predictions to JSON) is unchanged.

A recording that is cut short (window closed, power cut) is still readable: rows
are written to disk as they arrive, Ctrl+C and SIGTERM finish the take
properly, and on the next start any take with no final summary has it rebuilt
from the CSV (a half-written last row is dropped).

## REST endpoints

| Method | Path | Body / response |
|--------|------|-----------------|
| GET | `/` | serves `static/index.html` |
| GET | `/api/storage` | absolute `root`, `captures`, `sessions`, `models`, `exports`, `logs`; `free_bytes`, `low_space` (under 1 GB); `reveal_enabled` (true only for a caller on the server computer, never under `--debug`); `legacy_hint` |
| POST | `/api/reveal` | body `{folder?: root\|captures\|sessions\|models\|exports\|logs, name?: capture}` opens that folder (or highlights the capture) in the file manager on the server computer. 403 for callers that are not local. |
| GET | `/api/sessions/{id}/export?raw=0\|1&notes=0\|1` | downloads the session as a zip (`openmuscle-session-<id>.zip`); 404 unknown session, 409 while one of its takes is still recording |
| GET | `/api/sessions/{id}/export/estimate?raw=&notes=` | `{takes, files, estimated_zip_bytes, compact_zip_bytes, with_raw_zip_bytes, warn_large, warn_over_bytes, warnings}` |
| POST | `/api/sessions/{id}/export` | body `{raw?, notes?}` builds the same zip into the exports folder and returns `{out_file, filename, takes, files, zip_bytes, ...}` |
| POST | `/api/export` | body `{captures: [...], raw?, notes?}` downloads loose takes as a zip; 404 for a missing take, 400 for a bad name |
| POST | `/api/export/estimate` | same body, returns the size estimate |
| GET | `/static/*` | static assets (no-cache headers in dev) |
| WS  | `/ws/live` | server-pushes the `tick` snapshot above on every batch of packets |
| GET | `/api/devices` | same `devices` array as in the tick |
| GET | `/api/mode` | `{debug, app: "openmuscle", version}`; `app` lets a launcher or a second instance tell this server from any other program on the port |
| GET | `/api/discovery` | the known bands as a list. `?detail=1` returns `{devices, pc_host, interface, listener_ok: {announce, data}, listener_errors, ports, last_announce_s, announces_per_min, last_discover_s, ...}` instead (same as `/api/discovery/status`). `pc_host` is this computer's address on the bands' network, worked out per device with a UDP connect to the device's own IP (never loopback for a band on the network) and re-worked on every resubscribe and heartbeat |
| GET | `/api/discovery/status` | the detail view above |
| POST | `/api/discovery/discover` | broadcast the PROTOCOL 5.5 `{"verb":"discover"}` probe; every device answers at once. Returns `{found: [device_id], devices}`. The server also sends it at startup, every 10 s, and whenever this computer's address changes |
| POST | `/api/discovery/scan` | "Search my network": probe the addresses around this computer with `get_info`. Body `{prefix?: 24 or 16, max_hosts?: up to 1024, start?, end?, timeout?}`. Returns `{found, devices}` |
| GET | `/api/discovery/diagnose` | the self-diagnosis behind `openmuscle doctor`: `{ok, checks: [{id, title, status: pass/warn/fail, detail, fix}], udp_port, bound_host, devices_seen: [{device_id, ip, age_s}], last_announce_s, firewall: {platform, likely_blocked, how_to_fix}, likely_cause, advice: {sentence, action}}`. `likely_cause` is one of `none`, `firewall`, `different_network`, `client_isolation`, `device_offline`, `port_conflict`, `no_network`. `?discover=0` skips the live probe |
| POST | `/api/support-report` | writes a zip (versions, doctor results, recent log lines; no Wi-Fi names, passwords or capture data) into `~/OpenMuscle/exports`; returns `{ok, path, size}` |
| GET | `/api/recording` | `{recording, filename, sensor_device_id, label_device_id, window_ms, rows, duration_s, matched, unpaired_sensor, match_rate, ...}` or `{recording: false}` |
| POST | `/api/recording` | body `{sensor_device_id?, label_device_id?, filename?, window_ms?}`: starts a paired capture. All fields optional. `sensor_device_id` defaults to first flexgrid; `label_device_id` defaults to first lask5. Pass `label_device_id: ""` to record sensor-only (no pairing). |
| DELETE | `/api/recording` | stops the active capture, returns final stats + sidecar paths |
| GET | `/api/captures` | list of `{name, size_bytes, mtime, rows, duration_s, quality, ...}` for files in `--captures-dir`; `quality` is `{verdict, reasons, flags}` or null for takes not scored yet |
| GET | `/api/captures/{name}/quality` | take quality `{verdict: good\|check\|bad, reasons[], flags[], source}`. Takes recorded by this version store it in the meta sidecar (`auto.final`) at stop; for older takes the CSV is scanned once and the result cached. `?refresh=true` rescans. The thresholds live in `openmuscle/quality.py`. |
| GET | `/api/captures/{name}/download` | CSV file |
| DELETE | `/api/captures/{name}` | remove the capture file + its `.sensor.jsonl` / `.label.jsonl` sidecars |
| POST | `/api/train` | body `{captures: [name1, name2, ...], model_type?, n_estimators?, test_split?, activate?, role?, holdout_captures?, labels?}`; `labels` is `curls` (default) or `full`: for a 175-column hand skeleton `curls` trains only the five finger curls (`lbl_flex_*`), which is what the score measures and keeps the model to a few MB to tens of MB, while `full` keeps every hand column (hundreds of MB, needed only to draw the predicted 3D hand). The forest is capped either way: at most 100 trees and a minimum leaf size that rises with the number of rows to stay under a size budget (`metrics.n_estimators`, `metrics.leaf_min_rows`, `metrics.label_set`, `metrics.est_model_mb`); combines the named captures (oldest first by recording time), scores the model honestly, fits the final model on all rows, optionally hot-swaps it into the live inference engine. Returns `{model_path, metrics, verdict, active, captures}`. The score is held out in time: with 2+ captures the last `test_split` share of them is held out whole (or the names in `holdout_captures`); with one capture the final `test_split` of the rows is held out after a ~1 s purge gap. `metrics`: `eval_method` (`held_out_capture` / `held_out_tail` / `none`), `eval_captures`, `n_eval_rows`, `curl_r2` and `curl_mae` (per-finger curl units, constant label columns ignored), `per_finger` (`[{finger, r2, mae, constant}]`, 5 fingers for hand-pose labels, 4 for LASK5), `n_constant_labels`, `r2`/`mse` (legacy, same held-out rows), `r2_random_split` (shuffled split, optimistic, kept only to compare with older models). `verdict`: `{grade, headline, detail, next_step, show_live_ok}`, see `ml/verdict.py`. Before anything is fitted the selection goes through `ml/checks.py`: a problem that would waste the run answers HTTP 400 with `{detail, checks}` where `detail` is a plain sentence (too few rows or seconds, a labeler that never moved, a band that was flat, labels all missing, left and right recordings that never overlap); softer problems come back in the 200 response as `checks: [{level: "warn", code, message, fix}]` (single recording, mixed band units, mixed participants or sessions, rows dropped for missing values). Library wording such as `n_samples` never reaches the client. A selection of left-only plus right-only recordings trains one model per hand automatically: the response then has `per_hand: true` and `results: {left, right}` (each a full result), with the top-level `verdict` being the weaker hand. Each result also carries `model_dir` (the saved folder) and `provenance` (participant, band unit ids per side, firmware, label source). |
| GET | `/api/models` | list of trained models with metadata (`{name, created, metrics:{curl_r2, per_finger, eval_method, r2, mse, n_features, n_labels, ...}, verdict, path, active, size_bytes}`; `size_bytes` is the model folder's size on disk). Newest first. Models trained before held-out scoring existed get `verdict.grade = "untested"`. Each entry also has `dir` (its unique folder name), `participant` (normalised code, `""` when the model carries none) and, for new models, `band_device_ids`, `firmware` and `label_source`. |
| DELETE | `/api/models/{name}` | delete one trained model folder (`name` is the folder name, `dir` in the list). Returns `{deleted, freed_bytes}`. 404 for a name that is not a model folder (no path can reach outside the models folder), 409 when the model is loaded in a prediction slot (load another first). Recordings are never touched. |
| POST | `/api/inference/model` | body `{path}`: hot-swap the live inference engine to a different model.pkl without restarting the server. Returns `{active, enabled, model, guard}`; `guard` lists participant / band-unit mismatches. `/api/inference/model/{left|right}` does the same for one hand's slot. The shared/left/right choice is saved to `<models>/active.json` and restored (paused, with a banner) on the next start; when a shared and a per-hand model are both loaded, the one activated last drives a band. |
| POST | `/api/inference/enabled` | body `{enabled: bool, force?: bool}`: start or pause prediction at runtime (engine stays loaded). Starting is refused with HTTP 409 `{detail, issues, can_override}` while a loaded model was trained for another participant code or for a different physical band unit than the one now on that arm; send `force: true` after the operator confirms. Returns `{enabled, model}`. |
| POST | `/api/inference/hand` | body `{host: str?, port?: int}`: set or clear the robot-hand UDP forwarding target. Pass `host: null` or empty string to disable forwarding. `port` defaults to 3145. Returns `{hand_target}`. |

OpenAPI docs at `/docs` once the server is running.

## Paired recording (sensor + label)

A single recording produces **three files** under `--captures-dir`:

| File | Format | Purpose |
|---|---|---|
| `<name>.csv` | schema-v2 row-major CSV: `ts_hub_ms, role, device_id, R0C0..R3Cn, label_0..label_M` | The trainable file. One row per sensor frame **that found a label within the temporal window**. Unpaired sensor frames are dropped. |
| `<name>.sensor.jsonl` | one packet per line: `{t, device_id, device_type, data}` | Raw sensor stream. Every received frame, regardless of pairing. |
| `<name>.label.jsonl`  | same shape | Raw label stream. Every received label packet. |
| `<name>.meta.json` | recording context + interop keys | `schema/mirror/label_source/roles/created_ms` at the top level (matches the phone sidecar) plus PC-specific provenance under `.auto`. |

The two JSONL sidecars exist so you can **re-pair offline with a different window** without re-capturing. A 5-line Python script using `TemporalMatcher(window_s=0.05)` over the two JSONL files reproduces the CSV with whatever temporal tolerance you want. Cheap insurance (~10-50 KB/s extra disk at typical rates).

**Schema v2.** The capture CSV is the multi-device schema v2 (the canonical spec is `OpenMuscle-Connect/docs/CSV-SCHEMA-V2.md`): each row is one source frame, tagged with `ts_hub_ms` (hub arrival, epoch ms), the hub-assigned `role` (lowercase `left`/`right`/`labeler`), and the source `device_id`, followed by row-major `R{r}C{c}` features and the matched `label_*` values. A single-band capture is the long format with one role on every row; multiple bands interleave by arrival time, and the trainer pivots them into the wide Left||Right matrix (see `data/dataset.py pivot_bilateral`). Both `openmuscle web` and the CLI `openmuscle record` write this exact format.

### Pairing algorithm

`TemporalMatcher` (in `receiver/matcher.py`) is a deque-based nearest-neighbour matcher keyed on packet `receive_time`:

1. Every label packet appends to a deque.
2. On each sensor frame, the deque is pruned of labels older than `receive_time - window_s`.
3. The remaining labels are linearly scanned for the one with the smallest `|gap|`. That label is emitted as the pair. If the deque is empty after pruning, the sensor frame is counted as `unpaired` and dropped from the CSV.

Default window is **100 ms** -- at 25 Hz LASK5 input that's ~2-3 labels in window, so the matcher almost always has a candidate. Tune via the POST body's `window_ms` field, or in the UI via the (forthcoming) window slider. Tight windows reject more sensor frames but give crisper alignment; loose windows pair more aggressively but accept stale labels.

The same `TemporalMatcher` runs in the CLI's `openmuscle record` command, so paired CSVs are byte-equivalent regardless of capture source.

### CSV column order (row-major; do not rearrange)

CSVs are written **row-major** so the column headers `R0C0, R0C1, ..., R0Cn, R1C0, ...` correspond directly to the cell at `(row=r, col=c)`. Earlier (pre-commit `245cb8f`) the writer flattened col-major while the header was row-major, which silently transposed the meaning of every column and confused analysis. Old captures written before that commit need to be re-interpreted with col-major rule. `web/inference.py` flattens FlexGrid frames the same way before calling `model.predict()`, so feature order at inference matches training.

### UI device pickers

Two dropdowns in the Record panel let you override the auto-pick (first flexgrid + first lask5). Selections persist to `localStorage`, so on next page load you don't have to re-pick. While a recording is active both dropdowns are disabled to keep the matcher's input streams stable.

## Multi-capture training

The typical workflow is: record several short sessions of different motions (open/close, point, grip, ...) and train one model over all of them.

**From the UI:**
1. Make N recordings; each shows up in the Captures panel.
2. Tick the checkboxes for the ones you want to include (or the header-row check-all).
3. Click **Train**. The captures are combined row-wise into one training set, an RF regressor is fit, the trained model lands in `~/OpenMuscle/models/random_forest_<timestamp>/`, and (if `activate: true`, the default) the engine is **hot-swapped to use the new model immediately**, no server restart.
4. The new model appears in the **Models** panel below with R², MSE, feature/label counts. Click **use** on any other model to switch back.

Training runs in `asyncio.to_thread()` so the WS broadcast and packet ingest keep flowing for other clients during the fit. On a typical RF with 100 trees + 50k rows + 60 features this is ~10-30s.

**From the CLI:**
```
openmuscle train ~/OpenMuscle/captures/session_a.csv \
                 ~/OpenMuscle/captures/session_b.csv \
                 ~/OpenMuscle/captures/session_c.csv
```

Variadic positional args: the CLI concats the CSVs to a temp file (same `combine_csvs` helper the web uses), trains, and removes the temp. Single-file invocation still works (`openmuscle train one.csv`).

**Schema requirement:** all selected captures must share the same column layout (same matrix shape + same label count + same optional imu/forearm columns). `combine_csvs` now validates every file's header up front and **refuses to merge mismatched schemas** (raises with a column-shape diff) instead of silently producing a ragged CSV. Same-schema captures (including left+right, which differ only by row values) concat cleanly. Mixing V1 (12-sensor) and V4 (60-sensor) captures, or a LASK5 (4-label) with a Quest (175-label) capture, is rejected: keep them in separate model lineages, or pool different label sources via the canonical-label merge (see `docs/data-schema.md`), not `combine_csvs`.

## ML inference (`--model` / `--hand`)

The **Ground truth vs Predicted** area runs live when you pass `--model PATH`. Pipeline:

```
FlexGrid UDP packet ──> AppState._handle_packet
                             │
                             ↓ flatten row-major (R0C0..R0Cn, R1C0..)
                       InferenceEngine.predict(matrix)
                             │
                  ┌──────────┴──────────┐
                  ↓                     ↓
            WS snapshot              if --hand HOST[:PORT] set:
            inference.piston_values  send 'PC,a1,a2,a3,a4,a5' UDP datagram
                                     to robot hand (hand maps linearly
                                     0..179 → 0..179° per its 'PC' device)
```

The 5th servo angle for the hand is the **joystick X from the most recent LASK5 packet** (passed through, not predicted), per the design call that the model only predicts the 4 pistons it was trained on. If no LASK5 has been seen this session, finger-0 stays at 90°.

**Code:**
- `web/inference.py`: `InferenceEngine` class. Loads `model.pkl` + sibling `metadata.json`, validates input shape against `n_features_in_` / metadata, returns `None` on mismatch (with `last_error` set, surfaced in the UI status line). No numpy dependency in the hot path; sklearn `predict()` is called with a list-of-lists.
- `web/state.py`: `AppState(model_path=..., hand_target=...)`. Owns the engine + a non-blocking UDP socket to the hand. `_forward_to_hand(pred)` clamps each predicted piston to [0, 1] (assumes normalized output; see caveat below) and scales to 0..179° before sending.
- `cli.py`: `--model PATH` and `--hand HOST[:PORT]` (port defaults to 3145 if omitted).

**Caveat (model output range):** the hand-forwarding code assumes predictions are in **0..1** (matching the modular LASK5 firmware's calibrated wire format). Older models trained on raw ADC values (e.g. our `random_forest_20260321_110750`) emit numbers in the 2000–4000 range; the clamp will saturate the hand at 179° for every finger. The web UI's piston bars handle either range fine (the frontend's `pistonFraction` auto-detects fraction vs raw), but **you'll want to retrain on data captured from the current modular LASK5** before the hand will visibly track predictions. The same model-shape check that catches feature-count mismatches will also tell you in the status line if you point the engine at an incompatible model.

### Runtime inference controls

The inference pipeline can be reconfigured live without restarting the server:

1. **Predict toggle** (`#infer-toggle` in the control bar): gates `_handle_packet`'s inference call. The engine stays loaded while paused (no reload cost on resume); predictions just stop flowing and the status reads `paused`. It is disabled, with the label "no model yet, train first", until a model has been loaded. Starting is refused with HTTP 409 when the loaded model's participant code or band units do not match, and the page asks before sending `force: true`.
2. **Hand target** (Diagnostics and logs, then **Output routing**): sets the UDP forwarding target for the robot hand. Type `10.0.0.17` or `10.0.0.17:3145` and press **Apply**. Clearing the field and applying disables forwarding. The badge next to it shows `forwarding` when active.
3. The toggle syncs with `inference.enabled` in the WS snapshot, so a `POST /api/inference/enabled` from elsewhere (for example curl) shows up without a refresh.

Loading a model (`POST /api/inference/model`, or **Load** in the Models table) does **not** start it: inference stays paused until the operator presses **Predict**. The exception is training from the Studio: after a model earns Fair or Good, the page calls `POST /api/inference/enabled` for you (`shouldAutoStart` in `static/train-card.js`), and a weaker model offers **Start anyway**. The `--model` flag at server start also auto-starts, because passing a model on the command line is an explicit "run this now". To load a model in the paused state, `POST /api/inference/enabled {enabled: false}` immediately after.

The shared, left and right slots are saved to `<models>/active.json` and restored (paused, with a banner) on the next start.

### Adding a new device type

The WS snapshot already exposes `matrix`, `values`, and `joystick` for any device. To support a new payload shape (e.g. an IMU stream with `{accel, gyro, mag}`):

1. Extend `DeviceInfo.update()` in `state.py` to extract whatever new keys come in `pkt.data`.
2. Add the new key(s) to the snapshot dict in `_snapshot()`.
3. Add a panel in `index.html` + render function in `app.js`. The pattern is identical to `renderLask()`.

### Listening on multiple UDP ports

Today there's exactly one `UDPListener` per `AppState`, on `--udp-port`. If you need to listen on more (e.g. legacy LASK5 on 3145 while keeping FlexGrid on 3141), the cleanest extension is to instantiate multiple `UDPListener`s in `AppState.__init__` and merge their queues in `run_broadcaster`. As of v0.2.0 we've standardized everything on 3141 instead, so this hasn't been needed yet.

## VR companion (`/vr`)

The same FastAPI process also serves a WebXR client that turns a Meta Quest 3 into a labeling rig and live demo for the muscle→finger model. Operator guide: [`docs/vr-setup.md`](../../../../docs/vr-setup.md). Wire format for the new device type: [`docs/protocol.md`](../../../../docs/protocol.md#quest-hand-tracking-type-quest_hand).

### What's added vs the bare web UI

| Surface | Purpose |
|---|---|
| `GET /vr` | Serves `static/vr/index.html`: landing page + WebXR client (Three.js + XRHand). |
| `WS /ws/quest` | Inbound: accepts XRHand joint frames from the headset. Each frame is synthesized in-process into an `OpenMusclePacket(device_type="quest_hand")` and fed through the same `_handle_packet` UDP devices use. From the recorder/matcher/snapshot's view the Quest is just another device. |
| `start-vr.bat` (in `pc/`) | One-click launcher: ADB sanity check → start server → poll until up → `adb reverse` → open Quest Browser to `/vr`. Optional arm arg (`right`\|`left`). |
| `--ssl-certfile` / `--ssl-keyfile` | Serve HTTPS so the headset can hit `/vr` over LAN. WebXR refuses hand-tracking on plain HTTP (localhost is the only exception, via `adb reverse`). |

### Why a WebSocket inbound (when everything else is UDP)

Browsers can't speak UDP. WebXR therefore can't run as a UDP-emitting device. The chosen workaround is one synthesizer method (`AppState.ingest_quest_packet`) that builds the same `OpenMusclePacket` shape the UDP listener emits, so the rest of the pipeline (`_handle_packet` → `DeviceInfo.update` → `_record_packet` → `TemporalMatcher` → `CaptureWriter`) is unchanged. The integration cost was one new endpoint plus one synthesizer; everything downstream rides for free.

### `quest_hand` recording specifics

- **Match window default** is 175 ms for `quest_hand` label sources vs 100 ms for `lask5`, set per-device-type in `AppState.DEFAULT_WINDOW_MS_BY_TYPE`. Quest WebXR has higher end-to-end latency than LASK5's ESP-NOW path, so a tighter window dropped too many sensor frames as unpaired.
- **`label_count` is None / lazy-inferred** for `quest_hand`: `CaptureWriter` defers the CSV header write until the first label packet so the column count is derived from `len(values)`. Quest 3 sends 25 joints × 7 floats = 175 floats per frame; the hardcoded LASK5 `label_count=4` doesn't fit.
- **Per-capture `<name>.labels.schema.json` sidecar** lists joint names + channel order so the wide CSV is self-describing. Only emitted for label sources whose meaning isn't obvious from `device_type` alone (today: `quest_hand`).
- **`meta.json` `auto.label_source`** is tagged `"quest_hand"` so the Captures panel filter and any downstream training pipeline can cleanly separate Quest-labeled from LASK5-labeled datasets.
- **Auto-pick** in `start_recording` walks `AUTO_LABEL_TYPE_PREFERENCE = ("quest_hand", "lask5")`: Quest wins if both are connected, since it's the richer label source.

### WebXR client structure (`static/vr/`)

| File | Role |
|---|---|
| `index.html` | Landing page with a 3-checkmark preflight (HTTPS, WebXR support, server reachable), arm selector, VRButton mount. Script tag uses `?v=N` cache-buster (see gotcha below). |
| `app.js` | Scene + XR session lifecycle, per-frame joint capture → `/ws/quest`, real-time hand visualizer (blue captured-arm + green off-hand spheres), heatmap panel painted from `/ws/live`, 3×2 menu (REC / SESSION / PREDICT / TRAIN / RECENTER / EXIT VR), ray pointers + select-event button activation, REAL-vs-PRED finger-curl bars, ghost-hand overlay anchored at the real wrist when inference is on. |
| `styles.css` | Pre-VR landing page only (the XR session paints WebGL directly, CSS doesn't apply inside). |

### Cache-bust contract

The HTML references the JS as `app.js?v=N`. **Bump N every time `app.js` changes.** Quest Browser ignores `Cache-Control: no-store` for ES modules in some configurations, so the querystring forces a fresh fetch. The no-cache middleware on `/`, `/vr`, and `/static/*` is correct, but the version-querystring is the actual cache-busting mechanism in practice.

### Auto-enable inference on train (VR-only)

The desktop UI's server-side default is **paused-on-load** for inference (see commit `bd1b68a` rationale). In VR there's no obvious second click to enable it, so `runTrain` in `app.js` POSTs `{enabled: true}` to `/api/inference/enabled` after a successful activate, because pressing TRAIN implies "I want predictions running." The status strip surfaces `trained: R²=X · model loaded ✓ · predict ON` so the change is visible.

## Known gotchas

- **`from __future__ import annotations` breaks FastAPI body inference.** Don't add it back to `app.py`: the lazy-string annotations make FastAPI treat Pydantic-model parameters as query fields. (Bit us once, documented in `app.py` header.)
- **`Pin.init(Pin.OUT, value=0)` quirks** are in the firmware, not here, but if you ever rewrite the matrix scan, see the firmware repo's "Sensor scan techniques" section first.
- **Browser cache during dev**: the no-cache middleware on `/`, `/vr`, and `/static/*` makes JS/CSS edits land on plain F5 in desktop browsers. Quest Browser ignores it for ES modules; see the cache-bust contract in the VR section above (`?v=N` on the script src).
- **`/vr` was missing from the no-cache middleware** until commit `fb83f82`. If you add another HTML entry point, remember to whitelist it too.
- **mpremote and the LASK5 don't coexist** on the same serial port: `openmuscle web` only uses UDP, but Thonny / PuTTY / another mpremote session will lock the COM port. Symptom: `mpremote: failed to access COMxx`.
- **WebXR requires a secure context.** localhost (`http://`) counts; LAN HTTPS via mkcert is the untethered path. Plain HTTP over LAN will silently refuse to grant hand-tracking and the user sees "WebXR not available" with no specific reason. The landing-page preflight surfaces this.


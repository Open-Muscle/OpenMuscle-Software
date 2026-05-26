# `openmuscle web` — browser UI

A single-page FastAPI app for live monitoring, recording, and (eventually) live ML inference against OpenMuscle devices on the local network. Companion to the `openmuscle receive` matplotlib viewer; same UDP listener under the hood, just rendered in a browser instead.

## Run it

```
pip install -e .             # if not already (uses fastapi, uvicorn, pydantic)
openmuscle web               # defaults: HTTP :8000, UDP :3141
# then open http://localhost:8000
```

Options:
```
openmuscle web --port 8000 --udp-port 3141 --captures-dir data/raw/merged
openmuscle web --model data/models/random_forest_*/model.pkl   # live ML inference
openmuscle web --model X --hand 10.0.0.55                      # also forward to robot hand
openmuscle web --model X --hand 10.0.0.55:3145                 # custom hand port
```

First-run on Windows: Defender will prompt for inbound UDP — click **Allow** on both Private and Public, or pre-add: `New-NetFirewallRule -DisplayName "OpenMuscle UDP 3141" -Direction Inbound -Protocol UDP -LocalPort 3141 -Action Allow` (admin PowerShell).

## What you see

Five panels, all live:

| Panel | Source | Notes |
|-------|--------|-------|
| **Devices** | every parsed UDP packet | One row per `device_id`. Hz, packet count, last-seen age. Click to select for heatmap. |
| **Heatmap** | flexgrid matrix | Auto-detects shape (V1 16×4, V3 15×4, future ?×?). Color ramp goes black→purple→pink→orange→yellow, vmax auto-scales upward. Live `max=` and `vmax=` shown in the header. |
| **LASK5 — Ground Truth** | LASK5 `data.values` + `data.joystick` | 4 vertical piston bars (pink) + mini joystick canvas with crosshair + raw X,Y. Empty-state "no device" if no LASK5 has streamed. |
| **LASK Inference — Predicted** | `inference` slot in the WS snapshot | 4 blue piston bars + status line. Dimmed and "no model loaded" until the inference plug-in is wired up. |
| **Record / Captures** | local filesystem | Start/stop recording. CSV lands in `--captures-dir` (default `data/raw/merged/`). Captures list refreshes every 5 s; download or delete inline. |

## Architecture

```
pc/src/openmuscle/web/
├── app.py            FastAPI app: routes, lifespan, WS endpoint, no-cache middleware
├── state.py          AppState: owns UDPListener, DeviceInfo registry, recording,
│                     WebSocket clients, broadcaster task. Single shared instance per
│                     process.
└── static/
    ├── index.html    Single-page UI skeleton. 3-col grid layout.
    ├── app.js        WebSocket client + canvas heatmap + piston-bar + joystick
    │                 renderers + REST recording controls. Vanilla JS, no build step.
    └── styles.css    Dark theme. Grid template areas: devices / heatmap / lask /
                      infer / record / captures.
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

The `_snapshot()` dict pushed to every connected browser is the contract:

```json
{
  "type": "tick",
  "devices": [{"device_id":..., "device_type":..., "hz":...,
               "matrix": [[...]],          // flexgrid
               "values": [...],            // lask5 pistons / 1-D payloads
               "joystick": {"x":..,"y":..} // lask5 only
              }, ...],
  "recording": null,
  "inference": {"available": false, "model": null, "piston_values": null,
                "status": "no model loaded"}
}
```

## REST endpoints

| Method | Path | Body / response |
|--------|------|-----------------|
| GET | `/` | serves `static/index.html` |
| GET | `/static/*` | static assets (no-cache headers in dev) |
| WS  | `/ws/live` | server-pushes the `tick` snapshot above on every batch of packets |
| GET | `/api/devices` | same `devices` array as in the tick |
| GET | `/api/recording` | `{recording, filename, sensor_device_id, label_device_id, window_ms, rows, duration_s, matched, unpaired_sensor, match_rate, ...}` or `{recording: false}` |
| POST | `/api/recording` | body `{sensor_device_id?, label_device_id?, filename?, window_ms?}` — starts a paired capture. All fields optional. `sensor_device_id` defaults to first flexgrid; `label_device_id` defaults to first lask5. Pass `label_device_id: ""` to record sensor-only (no pairing). |
| DELETE | `/api/recording` | stops the active capture, returns final stats + sidecar paths |
| GET | `/api/captures` | list of `{name, size_bytes, mtime}` for files in `--captures-dir` |
| GET | `/api/captures/{name}/download` | CSV file |
| DELETE | `/api/captures/{name}` | remove the capture file + its `.sensor.jsonl` / `.label.jsonl` sidecars |
| POST | `/api/train` | body `{captures: [name1, name2, ...], model_type?, n_estimators?, test_split?, activate?}` — combines the named captures into one training set, fits a model, optionally hot-swaps it into the live inference engine. Returns `{model_path, metrics, active, captures}`. |
| GET | `/api/models` | list of trained models with metadata (`{name, created, metrics:{r2, mse, n_features, n_labels, ...}, path, active}`). Newest first. |
| POST | `/api/inference/model` | body `{path}` — hot-swap the live inference engine to a different model.pkl without restarting the server. |
| POST | `/api/inference/enabled` | body `{enabled: bool}` — pause or resume inference at runtime (engine stays loaded; predictions stop flowing). Returns `{enabled, model}`. |
| POST | `/api/inference/hand` | body `{host: str?, port?: int}` — set or clear the robot-hand UDP forwarding target. Pass `host: null` or empty string to disable forwarding. `port` defaults to 3145. Returns `{hand_target}`. |

OpenAPI docs at `/docs` once the server is running.

## Paired recording (sensor + label)

A single recording produces **three files** under `--captures-dir`:

| File | Format | Purpose |
|---|---|---|
| `<name>.csv` | row-major CSV: `timestamp, R0C0..R3Cn, label_0..label_3` | The trainable file. One row per sensor frame **that found a label within the temporal window**. Unpaired sensor frames are dropped. |
| `<name>.sensor.jsonl` | one packet per line: `{t, device_id, device_type, data}` | Raw sensor stream. Every received frame, regardless of pairing. |
| `<name>.label.jsonl`  | same shape | Raw label stream. Every received label packet. |

The two JSONL sidecars exist so you can **re-pair offline with a different window** without re-capturing. A 5-line Python script using `TemporalMatcher(window_s=0.05)` over the two JSONL files reproduces the CSV with whatever temporal tolerance you want. Cheap insurance (~10-50 KB/s extra disk at typical rates).

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
3. Hit **⚙ Train selected**. The captures are combined row-wise into one training set, an RF regressor is fit, the trained model lands in `data/models/random_forest_<timestamp>/`, and — if `activate: true`, the default — the engine is **hot-swapped to use the new model immediately**, no server restart.
4. The new model appears in the **Models** panel below with R², MSE, feature/label counts. Click **use** on any other model to switch back.

Training runs in `asyncio.to_thread()` so the WS broadcast and packet ingest keep flowing for other clients during the fit. On a typical RF with 100 trees + 50k rows + 60 features this is ~10-30s.

**From the CLI:**
```
openmuscle train data/raw/merged/session_a.csv \
                 data/raw/merged/session_b.csv \
                 data/raw/merged/session_c.csv
```

Variadic positional args: the CLI concats the CSVs to a temp file (same `combine_csvs` helper the web uses), trains, and removes the temp. Single-file invocation still works (`openmuscle train one.csv`).

**Schema requirement:** all selected captures must share the same column layout (same matrix shape + same label count). `combine_csvs` writes the first file's header verbatim; subsequent files contribute body rows only. If you mix V1 (12-sensor) and V3 (60-sensor) captures, you'll get garbage — keep them in separate model lineages.

## ML inference (`--model` / `--hand`)

The **LASK Inference — Predicted** panel runs live when you pass `--model PATH`. Pipeline:

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
- `web/inference.py` — `InferenceEngine` class. Loads `model.pkl` + sibling `metadata.json`, validates input shape against `n_features_in_` / metadata, returns `None` on mismatch (with `last_error` set, surfaced in the UI status line). No numpy dependency in the hot path; sklearn `predict()` is called with a list-of-lists.
- `web/state.py` — `AppState(model_path=..., hand_target=...)`. Owns the engine + a non-blocking UDP socket to the hand. `_forward_to_hand(pred)` clamps each predicted piston to [0, 1] (assumes normalized output — see caveat below) and scales to 0..179° before sending.
- `cli.py` — `--model PATH` and `--hand HOST[:PORT]` (port defaults to 3145 if omitted).

**Caveat — model output range:** the hand-forwarding code assumes predictions are in **0..1** (matching the modular LASK5 firmware's calibrated wire format). Older models trained on raw ADC values (e.g. our `random_forest_20260321_110750`) emit numbers in the 2000–4000 range; the clamp will saturate the hand at 179° for every finger. The web UI's piston bars handle either range fine (the frontend's `pistonFraction` auto-detects fraction vs raw), but **you'll want to retrain on data captured from the current modular LASK5** before the hand will visibly track predictions. The same model-shape check that catches feature-count mismatches will also tell you in the status line if you point the engine at an incompatible model.

### Runtime inference controls

The **LASK Inference — Predicted** panel now exposes three things below the bars, so the inference pipeline can be reconfigured live without restarting the server:

1. **▶ Resume / ⏸ Pause toggle**: gates `_handle_packet`'s inference call. The engine stays loaded while paused (no reload cost on resume); predictions just stop flowing and the panel shows `status: paused`. Disabled until a model has been loaded — load one via the Models panel's `use` button or `POST /api/inference/model`.
2. **→ Hand `<host[:port]>` input**: sets the UDP forwarding target for the robot hand. Type `10.0.0.17` or `10.0.0.17:3145`, press Enter or click Apply. Clearing the field and applying disables forwarding entirely. The state badge next to the input shows `● forwarding` (green) when active.
3. The button auto-syncs with `inference.enabled` in the WS snapshot so a `POST /api/inference/enabled` from elsewhere (e.g. curl) is reflected in the UI without a refresh.

Loading a model (`POST /api/inference/model` or the Models panel's `use` button) implicitly **resumes inference** — the intuition is "if you just clicked use on a model, you want predictions to start". If you want to load a model in the paused state, `POST /api/inference/enabled {enabled: false}` immediately after.

### Future: model hot-swap UI

A dropdown in the UI that lists `data/models/*.pkl` and reloads `AppState.engine` on selection would be a clean follow-up — `InferenceEngine.__init__` already does all the work, so it's a single REST endpoint + a `<select>` in `app.js`.

### Adding a new device type

The WS snapshot already exposes `matrix`, `values`, and `joystick` for any device. To support a new payload shape (e.g. an IMU stream with `{accel, gyro, mag}`):

1. Extend `DeviceInfo.update()` in `state.py` to extract whatever new keys come in `pkt.data`.
2. Add the new key(s) to the snapshot dict in `_snapshot()`.
3. Add a panel in `index.html` + render function in `app.js`. The pattern is identical to `renderLask()`.

### Listening on multiple UDP ports

Today there's exactly one `UDPListener` per `AppState`, on `--udp-port`. If you need to listen on more (e.g. legacy LASK5 on 3145 while keeping FlexGrid on 3141), the cleanest extension is to instantiate multiple `UDPListener`s in `AppState.__init__` and merge their queues in `run_broadcaster`. As of v0.2.0 we've standardized everything on 3141 instead, so this hasn't been needed yet.

## VR companion (`/vr`)

The same FastAPI process also serves a WebXR client that turns a Meta Quest 3 into a labeling rig and live demo for the muscle→finger model. Operator guide: [`docs/vr-setup.md`](../../../../../docs/vr-setup.md). Wire format for the new device type: [`docs/protocol.md`](../../../../../docs/protocol.md#quest-hand-tracking-type-quest_hand).

### What's added vs the bare web UI

| Surface | Purpose |
|---|---|
| `GET /vr` | Serves `static/vr/index.html` — landing page + WebXR client (Three.js + XRHand). |
| `WS /ws/quest` | Inbound — accepts XRHand joint frames from the headset. Each frame is synthesized in-process into an `OpenMusclePacket(device_type="quest_hand")` and fed through the same `_handle_packet` UDP devices use. From the recorder/matcher/snapshot's view the Quest is just another device. |
| `start-vr.bat` (in `pc/`) | One-click launcher: ADB sanity check → start server → poll until up → `adb reverse` → open Quest Browser to `/vr`. Optional arm arg (`right`\|`left`). |
| `--ssl-certfile` / `--ssl-keyfile` | Serve HTTPS so the headset can hit `/vr` over LAN. WebXR refuses hand-tracking on plain HTTP (localhost is the only exception, via `adb reverse`). |

### Why a WebSocket inbound (when everything else is UDP)

Browsers can't speak UDP. WebXR therefore can't run as a UDP-emitting device. The chosen workaround is one synthesizer method (`AppState.ingest_quest_packet`) that builds the same `OpenMusclePacket` shape the UDP listener emits, so the rest of the pipeline — `_handle_packet` → `DeviceInfo.update` → `_record_packet` → `TemporalMatcher` → `CaptureWriter` — is unchanged. The integration cost was one new endpoint plus one synthesizer; everything downstream rides for free.

### `quest_hand` recording specifics

- **Match window default** is 175 ms for `quest_hand` label sources vs 100 ms for `lask5`, set per-device-type in `AppState.DEFAULT_WINDOW_MS_BY_TYPE`. Quest WebXR has higher end-to-end latency than LASK5's ESP-NOW path, so a tighter window dropped too many sensor frames as unpaired.
- **`label_count` is None / lazy-inferred** for `quest_hand` — `CaptureWriter` defers the CSV header write until the first label packet so the column count is derived from `len(values)`. Quest 3 sends 25 joints × 7 floats = 175 floats per frame; the hardcoded LASK5 `label_count=4` doesn't fit.
- **Per-capture `<name>.labels.schema.json` sidecar** lists joint names + channel order so the wide CSV is self-describing. Only emitted for label sources whose meaning isn't obvious from `device_type` alone (today: `quest_hand`).
- **`meta.json` `auto.label_source`** is tagged `"quest_hand"` so the Captures panel filter and any downstream training pipeline can cleanly separate Quest-labeled from LASK5-labeled datasets.
- **Auto-pick** in `start_recording` walks `AUTO_LABEL_TYPE_PREFERENCE = ("quest_hand", "lask5")` — Quest wins if both are connected, since it's the richer label source.

### WebXR client structure (`static/vr/`)

| File | Role |
|---|---|
| `index.html` | Landing page with a 3-checkmark preflight (HTTPS, WebXR support, server reachable), arm selector, VRButton mount. Script tag uses `?v=N` cache-buster (see gotcha below). |
| `app.js` | Scene + XR session lifecycle, per-frame joint capture → `/ws/quest`, real-time hand visualizer (blue captured-arm + green off-hand spheres), heatmap panel painted from `/ws/live`, 3×2 menu (REC / SESSION / PREDICT / TRAIN / RECENTER / EXIT VR), ray pointers + select-event button activation, REAL-vs-PRED finger-curl bars, ghost-hand overlay anchored at the real wrist when inference is on. |
| `styles.css` | Pre-VR landing page only (the XR session paints WebGL directly, CSS doesn't apply inside). |

### Cache-bust contract

The HTML references the JS as `app.js?v=N`. **Bump N every time `app.js` changes.** Quest Browser ignores `Cache-Control: no-store` for ES modules in some configurations — the querystring forces a fresh fetch. The no-cache middleware on `/`, `/vr`, and `/static/*` is correct, but the version-querystring is the actual cache-busting mechanism in practice.

### Auto-enable inference on train (VR-only)

The desktop UI's server-side default is **paused-on-load** for inference (see commit `bd1b68a` rationale). In VR there's no obvious second click to enable it, so `runTrain` in `app.js` POSTs `{enabled: true}` to `/api/inference/enabled` after a successful activate — pressing TRAIN implies "I want predictions running." The status strip surfaces `trained: R²=X · model loaded ✓ · predict ON` so the change is visible.

## Known gotchas

- **`from __future__ import annotations` breaks FastAPI body inference.** Don't add it back to `app.py` — the lazy-string annotations make FastAPI treat Pydantic-model parameters as query fields. (Bit us once, documented in `app.py` header.)
- **`Pin.init(Pin.OUT, value=0)` quirks** are in the firmware, not here — but if you ever rewrite the matrix scan, see the firmware repo's "Sensor scan techniques" section first.
- **Browser cache during dev**: the no-cache middleware on `/`, `/vr`, and `/static/*` makes JS/CSS edits land on plain F5 in desktop browsers. Quest Browser ignores it for ES modules — see the cache-bust contract in the VR section above (`?v=N` on the script src).
- **`/vr` was missing from the no-cache middleware** until commit `fb83f82`. If you add another HTML entry point, remember to whitelist it too.
- **mpremote and the LASK5 don't coexist** on the same serial port — `openmuscle web` only uses UDP, but Thonny / PuTTY / another mpremote session will lock the COM port. Symptom: `mpremote: failed to access COMxx`.
- **WebXR requires a secure context.** localhost (`http://`) counts; LAN HTTPS via mkcert is the untethered path. Plain HTTP over LAN will silently refuse to grant hand-tracking and the user sees "WebXR not available" with no specific reason. The landing-page preflight surfaces this.

## v0.2.0 history (the LASK5 expansion)

| Commit | Change |
|--------|--------|
| `d631951` | initial web UI skeleton — heatmap + record + captures |
| `1de431a` | heatmap auto-detects matrix shape (V3 fix) |
| `245cb8f` | CSV writer made row-major (was silently transposed) |
| `e6febe6` | added LASK5 Ground Truth + ML Inference panels, 3-col grid layout |
| `4ccb6c1` | app.py: removed `__future__ annotations`, added no-cache middleware, Optional types |

Companion docs:
- Firmware that drives the heatmap: [`FlexGridV3-Firmware`](https://github.com/Open-Muscle/FlexGridV3-Firmware) (sensor scan techniques)
- Firmware that drives the LASK panel: [`embedded/devices/lask5_v2/`](../../../../embedded/devices/lask5_v2/) — and its [migration plan](../../../../embedded/devices/lask5_v2/MIGRATION_FROM_MONOLITHIC.md) for the live device that's still on the older monolithic firmware

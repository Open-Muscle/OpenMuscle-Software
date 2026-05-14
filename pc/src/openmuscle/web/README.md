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
| GET | `/api/recording` | `{recording, filename, device_id, rows, duration_s}` or `{recording: false}` |
| POST | `/api/recording` | body `{device_id, filename?}` — starts a capture |
| DELETE | `/api/recording` | stops the active capture, returns final stats |
| GET | `/api/captures` | list of `{name, size_bytes, mtime}` for files in `--captures-dir` |
| GET | `/api/captures/{name}/download` | CSV file |
| DELETE | `/api/captures/{name}` | remove the capture file |

OpenAPI docs at `/docs` once the server is running.

## CSV recording

CSVs are written **row-major** so the column headers `R0C0, R0C1, ..., R0Cn, R1C0, ...` correspond directly to the cell at `(row=r, col=c)`. Earlier (pre-commit `245cb8f`) the writer flattened col-major while the header was row-major, which silently transposed the meaning of every column and confused analysis. Old captures written before that commit need to be re-interpreted with col-major rule.

## Extension points

### Wiring up ML inference (the obvious next thing)

The frontend already has the **LASK Inference — Predicted** panel rendering a `piston_values: [int, int, int, int]` array from the WS snapshot. Server-side, it currently returns a hardcoded "not loaded" placeholder. To wire it up:

1. Add an `InferenceEngine` class in `web/state.py` (or a new `web/inference.py`):
   - `__init__(model_path)` — load a `sklearn` / `numpy` / whatever model from `data/models/`.
   - `predict(flexgrid_matrix) -> list[int]` — input a `[cols][rows]` matrix, return 4 piston predictions.
2. Hold an `InferenceEngine | None` on `AppState`. Create it lazily when the CLI flag/config asks for it.
3. In `_handle_packet`, when a flexgrid packet arrives, call `engine.predict(mat)` and store the result on `AppState.last_inference` with a timestamp.
4. Update `_inference_snapshot()` to return `{"available": True, "model": <name>, "piston_values": list, "status": "live"}`.
5. (Optional) Add a CLI flag `--model PATH` so you can launch with a specific trained model: `openmuscle web --model data/models/random_forest_*/model.pkl`.
6. (Optional) Surface a model picker in the UI — a dropdown that lists `data/models/*.pkl` and hot-swaps the loaded model.

The frontend will automatically un-dim the panel and start animating the blue bars as soon as the snapshot's `inference.available` flips to `true`.

### Adding a new device type

The WS snapshot already exposes `matrix`, `values`, and `joystick` for any device. To support a new payload shape (e.g. an IMU stream with `{accel, gyro, mag}`):

1. Extend `DeviceInfo.update()` in `state.py` to extract whatever new keys come in `pkt.data`.
2. Add the new key(s) to the snapshot dict in `_snapshot()`.
3. Add a panel in `index.html` + render function in `app.js`. The pattern is identical to `renderLask()`.

### Listening on multiple UDP ports

Today there's exactly one `UDPListener` per `AppState`, on `--udp-port`. If you need to listen on more (e.g. legacy LASK5 on 3145 while keeping FlexGrid on 3141), the cleanest extension is to instantiate multiple `UDPListener`s in `AppState.__init__` and merge their queues in `run_broadcaster`. As of v0.2.0 we've standardized everything on 3141 instead, so this hasn't been needed yet.

## Known gotchas

- **`from __future__ import annotations` breaks FastAPI body inference.** Don't add it back to `app.py` — the lazy-string annotations make FastAPI treat Pydantic-model parameters as query fields. (Bit us once, documented in `app.py` header.)
- **`Pin.init(Pin.OUT, value=0)` quirks** are in the firmware, not here — but if you ever rewrite the matrix scan, see the firmware repo's "Sensor scan techniques" section first.
- **Browser cache during dev**: the no-cache middleware on `/` and `/static/*` makes JS/CSS edits land on plain F5. If you ever serve this off a CDN or behind a cache, remove or scope down that middleware.
- **mpremote and the LASK5 don't coexist** on the same serial port — `openmuscle web` only uses UDP, but Thonny / PuTTY / another mpremote session will lock the COM port. Symptom: `mpremote: failed to access COMxx`.

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

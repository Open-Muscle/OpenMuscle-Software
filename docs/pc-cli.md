# OpenMuscle CLI

The `openmuscle` command-line tool provides a unified interface for all PC-side operations.

## Installation

```bash
cd pc/
pip install -e .          # Basic install
pip install -e ".[dev]"   # With test/lint tools
pip install -e ".[all]"   # Everything including VisPy 3D viz
```

## Commands

### `openmuscle receive`

Listen for devices and display a live heatmap.

```bash
openmuscle receive                          # Default port 3141
openmuscle receive --port 3141 --save-dir data/raw/flexgrid
```

### `openmuscle web`

Launch the browser-based Studio UI: a live FlexGrid heatmap, device/source
discovery, recording + labeled sessions, training, and live prediction, with a
WebXR companion at `/vr` for Quest hand-tracking capture.

```bash
openmuscle web                      # http://localhost:8000, this computer only;
                                    #   opens your browser and prints where data is saved
openmuscle web --port 8000 --udp-port 3141
openmuscle web --model-left L.pkl --model-right R.pkl   # per-hand live prediction
```

Defaults for a first run:

| Flag | Effect |
|---|---|
| (none) | Plain HTTP on `127.0.0.1:8000` (reachable from this computer only). Opens the page in your default browser once the server is up and prints a short banner: the address, the data folder, "leave this window open", and the firewall sentence. If port 8000 is busy it uses the next free port and says so. |
| `--no-browser` | Do not open the browser automatically. |
| `--lan` | Listen on all interfaces (`0.0.0.0`) so a phone or headset on the same Wi-Fi can open the page. Anyone on that network can reach it. |
| `--vr` | Headset workflow: implies `--lan` and serves https when a mkcert pair (`vr-cert.pem` + `vr-key.pem`) is found in `~/.openmuscle/`, in `OPENMUSCLE_SSL_CERTFILE` / `OPENMUSCLE_SSL_KEYFILE`, or the current directory. See [`vr-setup.md`](vr-setup.md). |
| `--ssl-certfile` / `--ssl-keyfile` | Serve https from an explicit cert/key pair (also opens the LAN, as the headset needs). |
| `--data-dir DIR` | Where captures, sessions and models are saved. Same as setting `OPENMUSCLE_HOME`. |
| `--captures-dir DIR` | Save captures somewhere specific (sessions go in a `sessions` folder next to it). |
| `--verbose` | Show server request logs and low-level startup lines. |

TLS is opt-in: a plain `openmuscle web` stays on http even if you have certificates
in `~/.openmuscle/`, and prints nothing about TLS, WebXR or adb.

**Where your files go.** Everything is saved under one folder, `~/OpenMuscle`
(for example `C:\Users\you\OpenMuscle` on Windows), no matter which directory you
start the program from:

```
~/OpenMuscle/captures   recorded CSVs and their .meta.json / .labels.schema.json sidecars
~/OpenMuscle/sessions   one JSON file per labeled session
~/OpenMuscle/models     trained models (one folder per model)
~/OpenMuscle/exports    prediction exports
~/OpenMuscle/logs       server logs
```

Set the environment variable `OPENMUSCLE_HOME` (or pass `--data-dir`) to use a
different root; `~` is expanded on every OS. `GET /api/storage` returns the
absolute paths the running server uses. If you launch from a folder that still
holds an older `data/raw/merged` or `data/models` directory, the banner prints a
one-line hint: copy those files into the matching `~/OpenMuscle` subfolder to see
them.

While the server runs, the console prints one line when a band is found
(`Found FlexGrid band at 192.168.1.23`) and one if it goes quiet (`Lost FlexGrid
band at ...`).

**Debug mode** (`--debug`): serves plain HTTP on all interfaces (no TLS) so the PC,
phone, and Quest all connect with zero cert friction, and unlocks a verbose Debug
panel (per-device stream health, per-channel matrix stats, IMU raw + orientation,
forearm, a raw-frame inspector), both flexgrid heatmaps at once (side-by-side or
stacked), and a live capture-quality verdict while recording. The server is LAN-open
and unauthenticated in this mode: lab-bench / demo use only.

```bash
openmuscle web --debug              # http://<lan-ip>:8000, all devices, verbose UI
```

WebXR hand-tracking needs a secure context: either `openmuscle web --vr` with the
mkcert pair in place (https over the LAN), or tether the Quest over USB with
`adb reverse tcp:8000 tcp:8000` and open `http://localhost:8000/vr`.

### `openmuscle record`

Record paired FlexGrid + LASK5 data to CSV with temporal matching.

```bash
openmuscle record -o ~/OpenMuscle/captures/session_01.csv
openmuscle record -o capture.csv --port 3141 --duration 60
```

### `openmuscle train`

Train an ML model from captured data.

```bash
openmuscle train ~/OpenMuscle/captures/session_01.csv
openmuscle train data.csv -o ~/OpenMuscle/models/my_model.pkl --trees 200 --test-split 0.3
```

### `openmuscle predict`

Run real-time inference with live visualization.

```bash
openmuscle predict -m ~/OpenMuscle/models/random_forest_20250101_120000/model.pkl
openmuscle predict -m my_model.pkl --port 3141
```

### `openmuscle simulate`

Send synthetic data for testing without hardware.

```bash
openmuscle simulate --device-type flexgrid
openmuscle simulate --device-type lask5 --target-ip 192.168.1.100
openmuscle simulate --replay data/raw/legacy/capture_45.txt

# Synthetic WebXR hand: streams 25-joint frames to the running
# `openmuscle web` server's /ws/quest WebSocket (no headset needed).
openmuscle simulate --device-type quest_hand

# Combo: the same latent finger-curl signal drives BOTH a flexgrid UDP
# device and the quest hand, so the capture is learnable and the whole
# record -> train -> predict pipeline can be exercised end to end with
# zero hardware. Use --web-port if the web server is not on 8000.
openmuscle simulate --device-type combo
```

### `openmuscle models`

List all trained models in the registry.

```bash
openmuscle models
```

## Typical Workflow

```bash
# 1. Capture training data (FlexGrid + LASK5 paired)
openmuscle record -o ~/OpenMuscle/captures/training_session.csv

# 2. Train a model
openmuscle train ~/OpenMuscle/captures/training_session.csv

# 3. Run real-time predictions
openmuscle predict -m ~/OpenMuscle/models/random_forest_*/model.pkl
```

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
openmuscle web                      # https://localhost:8000 (auto-loads a mkcert
                                    #   TLS pair from ~/.openmuscle if present)
openmuscle web --port 8000 --udp-port 3141
openmuscle web --model-left L.pkl --model-right R.pkl   # per-hand live prediction
```

**Debug mode** (`--debug`): serves plain HTTP on all interfaces (no TLS) so the PC,
phone, and Quest all connect with zero cert friction, and unlocks a verbose Debug
panel (per-device stream health, per-channel matrix stats, IMU raw + orientation,
forearm, a raw-frame inspector), both flexgrid heatmaps at once (side-by-side or
stacked), and a live capture-quality verdict while recording. The server is LAN-open
and unauthenticated in this mode: lab-bench / demo use only.

```bash
openmuscle web --debug              # http://<lan-ip>:8000, all devices, verbose UI
```

WebXR hand-tracking needs a secure context: either the auto-loaded HTTPS cert, or
tether the Quest over USB with `adb reverse tcp:8000 tcp:8000` and open
`http://localhost:8000/vr`.

### `openmuscle record`

Record paired FlexGrid + LASK5 data to CSV with temporal matching.

```bash
openmuscle record -o data/raw/merged/session_01.csv
openmuscle record -o capture.csv --port 3141 --duration 60
```

### `openmuscle train`

Train an ML model from captured data.

```bash
openmuscle train data/raw/merged/session_01.csv
openmuscle train data.csv -o data/models/my_model.pkl --trees 200 --test-split 0.3
```

### `openmuscle predict`

Run real-time inference with live visualization.

```bash
openmuscle predict -m data/models/random_forest_20250101_120000/model.pkl
openmuscle predict -m my_model.pkl --port 3141
```

### `openmuscle simulate`

Send synthetic data for testing without hardware.

**No hardware at all?** One command runs a whole V4 kit next to `openmuscle web`:

```bash
openmuscle web                                  # terminal 1
openmuscle simulate --device-type rehearsal     # terminal 2
```

`rehearsal` is the practice run. It starts two 15x4 pressure bands (60 cells each,
`flexgrid-sim-left` and `flexgrid-sim-right`) and a LASK5 (`lask5-sim`: four pistons
0..1 plus a joystick) at 25 Hz. The bands announce on the discovery beacon port and
answer the command channel, so they show up in the Sources list like real hardware, with
IMU and battery telemetry. Once discovered they are tagged left / right and the LASK5 as
labeler for you (`--no-tag-roles` to do it by hand in Sources). The bands and the LASK5
are driven by the same finger curls, with a rest then move schedule, so a capture you
record from them is learnable: record, train and predict give a sensible model. Left and
right have different per-cell gain and offset, so train one model per hand.

```bash
openmuscle simulate --device-type rehearsal --bands 1               # left band only
openmuscle simulate --device-type rehearsal --labeler webcam        # hand from a camera feed
openmuscle simulate --device-type rehearsal --labeler quest         # hand as a Quest would send it
openmuscle simulate --device-type rehearsal --noise high            # a weak dataset on purpose
openmuscle simulate --device-type rehearsal --noise 3 --seed 7      # numeric noise multiplier
```

`--noise` takes `low`, `default`, `high` or a number. `default` gives a held-out score
well above 0.6 after about 40 s of recording; `high` buries the signal (held-out score
near zero), which is handy for seeing how the app reports a weak dataset. The `webcam` and
`quest` labelers stream to the web server, over `wss://` automatically when the server
is HTTPS (`--web-scheme auto|http|https`). Use `--web-port` and `--announce-port` if the
server is not on 8000 / 3140.

The other device types below are plumbing tests only: `flexgrid` and `lask5` send random
noise, so a model trained on them learns nothing. `combo` is learnable too (a 16x4
flexgrid plus a Quest hand from the same curls).

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
openmuscle record -o data/raw/merged/training_session.csv

# 2. Train a model
openmuscle train data/raw/merged/training_session.csv

# 3. Run real-time predictions
openmuscle predict -m data/models/random_forest_*/model.pkl
```

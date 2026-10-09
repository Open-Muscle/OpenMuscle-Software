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
| `--no-keep-awake` | Let the computer sleep as usual. By default the computer is kept awake while OpenMuscle runs, so a recording is not cut off (Windows and macOS; nothing is done on Linux). Closing a laptop lid still puts it to sleep: that is the operating system's lid setting and is not overridden. |

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

**Only one OpenMuscle at a time.** If OpenMuscle is already running, a second
`openmuscle web` stops within a second with one line (`OpenMuscle is already running
at http://localhost:8000 ...`) and exits with a non-zero code instead of starting a
second server that would fight over the bands. The UDP data port (3141) is opened
exclusively for the same reason: if another program holds it, `openmuscle web` says
so and stops before anything starts. If only the discovery port (3140) is taken it
prints a note and keeps going (bands can still be found with the search buttons).
`GET /api/mode` includes `"app": "openmuscle"` so launchers can tell an OpenMuscle
server from any other program on the port.

### `openmuscle doctor`

Checks this computer and network in a few seconds and says what to fix. One line per
check, `PASS`, `WARN` or `FAIL`, with the fix under anything that is not a pass. The
exit code is 0 when nothing failed and 1 otherwise, so it can sit in a launcher.

```bash
openmuscle doctor                       # the normal check
openmuscle doctor --report              # also write openmuscle-report.txt to send to support
openmuscle doctor --report my-pc.txt    # ... with a file name of your choice
openmuscle doctor --port 8010 --udp-port 3141 --announce-port 3140
openmuscle doctor --no-discover         # skip the quick search for bands
```

It checks: the Python version and installed packages, the browser page files, that
the data folder can be written, the HTTP port (8000), the UDP data port (3141) and the
UDP discovery port (3140), this computer's network address, the Wi-Fi network name,
the Windows firewall rules and network type (Private or Public), internet access for
the 3D hand viewer, a self-test sensor frame sent to this computer, and a quick
"discover" broadcast to see which bands answer. It ends with the most likely reason a
band is not showing up: firewall, different network, client isolation (a guest
network or router setting that keeps devices apart), or the band being off.

The report is a plain text file with versions and these results. It never contains
Wi-Fi names, passwords or recordings. While it runs, Windows may ask whether to allow
Python through the firewall; choose Allow. The same checks run inside the app:
`GET /api/discovery/diagnose` returns them as JSON and `POST /api/support-report`
writes a zip (report, results, recent log lines with file names and secrets removed)
into `~/OpenMuscle/exports` and returns its path.

### `openmuscle firewall`

On Windows the bands cannot reach OpenMuscle until a firewall rule allows incoming UDP
on ports 3140 and 3141 for your network type. This command prints the exact rule and
the command behind it, asks, and only then opens the normal Windows administrator
prompt. It never runs by itself.

```bash
openmuscle firewall                # show the rule, ask, then add it (Private and Domain networks)
openmuscle firewall --print-only   # just print it
openmuscle firewall --public       # also allow on Public networks
openmuscle firewall --remove       # take the rule out again
```

The rule is `netsh advfirewall firewall add rule name="OpenMuscle UDP 3140-3141" dir=in
action=allow protocol=UDP localport=3140,3141 profile=private,domain`. On macOS and
Linux the command only explains what to allow.

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

### `openmuscle assets`

Offline copies of the browser files the webcam page and the 3D hand viewer use
(MediaPipe hand tracker, its model, three.js). Without them those pages load the
files from the internet.

```bash
openmuscle assets fetch              # download, verify checksums, install (about 17.6 MB + 1.3 MB)
openmuscle assets fetch --with-nosimd  # also the tracker files for processors without SIMD
openmuscle assets status             # which files are present and correct
openmuscle assets status --hashes    # also print each installed file's sha256
```

Files go to `<data folder>/assets` (default `~/OpenMuscle/assets`) and are served
under `/static/vendor/`. Every file is checked against a size and checksum pinned
in the code; a mismatch is discarded and nothing is installed for that file. See
[webcam-labeling.md](webcam-labeling.md).

## Typical Workflow

```bash
# 1. Capture training data (FlexGrid + LASK5 paired)
openmuscle record -o ~/OpenMuscle/captures/training_session.csv

# 2. Train a model
openmuscle train ~/OpenMuscle/captures/training_session.csv

# 3. Run real-time predictions
openmuscle predict -m ~/OpenMuscle/models/random_forest_*/model.pkl
```

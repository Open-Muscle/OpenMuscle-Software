# Architecture Overview

This page is for people who want to change the software. If you only want to use it, start with [getting-started.md](getting-started.md).

## System Diagram

```
                     your Wi-Fi (2.4 GHz)
  +-------------+                                     +-------------------------+
  | FlexGrid V4 |  UDP 3140: "I am here" beacons ---->|                         |
  | band (x2)   |<--- TCP 8001: subscribe + heartbeat-|   openmuscle web        |
  | 15x4 cells  |  UDP 3141: sensor frames ---------->|   (FastAPI, this        |
  +-------------+                                     |    computer only)       |
                                                      |                         |
  +-------------+  camera -> browser hand tracker --->|  discovery, recording,  |
  | webcam      |  joint positions over a WebSocket   |  temporal matching,     |
  +-------------+                                     |  training, prediction   |
                                                      |                         |
  +-------------+  optional:                          |                         |
  | LASK5       |  UDP 3141 label frames ------------>|                         |
  | headset     |  Quest hand joints over WebSocket ->|                         |
  +-------------+                                     +------------+------------+
                                                                   |
                                    browser (Chrome/Edge) <--------+ http://localhost:8000
                                                                   |
                                                         ~/OpenMuscle/  captures, sessions,
                                                                        models, exports
```

A band that has no Wi-Fi saved makes its own network named `OM-flexgrid-...` and serves a small setup page at `http://192.168.4.1`. The Studio's setup wizard (`/setup`) relays Wi-Fi details to that page; see [wifi-setup.md](wifi-setup.md).

## Directory Structure

```
OpenMuscle-Software/
    installers/                 One-click installers (windows/, macos/, linux/)
    embedded/
        lib/                    Shared firmware library (om_*.py)
        devices/
            flexgrid_v1/        Early FlexGrid firmware on the shared library
            lask5_v2/           LASK5 firmware on the shared library
            openhand_v2/        OpenHand robot hand firmware
            _template/          Template for new devices
    pc/
        src/openmuscle/         Python package
            protocol/           Packet schema + parser
            receiver/           UDP listener + temporal matcher + device registry
            discovery/          Finds bands (beacons, scan, probe) and subscribes to them
            provision/          Client for a band's Wi-Fi setup page
            data/               CSV storage, dataset loading, export bundles
            ml/                 Training checks, training, verdict words, inference, model registry
            web/                FastAPI app, app state, static browser files (the Studio)
            simulate/           Virtual sensor transmitter, rehearsal kit, capture replay
            viz/                Matplotlib visualizations
            doctor.py           Computer and network checks
            paths.py            The one data folder (~/OpenMuscle)
            cli.py              Click CLI entry point
        tests/                  pytest test suite
        pyproject.toml          Package config
    data/
        raw/                    Older sensor captures from earlier firmware
        samples/legacy/         Sample CSVs from the first bands
    docs/                       Documentation
    archive/                    Legacy code (preserved for reference)
```

Your own recordings and models are not stored in the repository. They go in `~/OpenMuscle` (see [your-data.md](your-data.md)).

## Key Design Decisions

1. **Shared firmware library** (`embedded/lib/`): copied to each device's `/lib` directory. Devices inherit from `BaseDevice` and implement `SensorInterface`. The shipping V4 band firmware is its own repository ([FlexGridV4-Firmware](https://github.com/Open-Muscle/FlexGridV4-Firmware)) that follows the same packet protocol.

2. **Standard packet protocol**: all devices send JSON over UDP with a versioned schema (`v`, `type`, `id`, `ts`, `data`, `meta`). See [protocol.md](protocol.md).

3. **Discover, then subscribe**: V4 devices do not know the computer's address. They announce themselves, the computer connects to the device's command port and subscribes, and the device then sends frames to the computer. Setup mode is the same idea one step earlier: the device offers a Wi-Fi network and a form.

4. **Backward-compatible parser**: the PC parser auto-detects legacy packet formats (bare arrays, Python repr strings) alongside the new protocol.

5. **One data folder**: captures, sessions, models and exports all live under one root, whichever directory you start from (`paths.py`).

6. **Honest scoring**: a model is scored on a recording or time span it never trained on, and the result is turned into plain words in one place (`ml/verdict.py`).

7. **Single CLI entry point**: all PC operations go through the `openmuscle` command (see [pc-cli.md](pc-cli.md)).

8. **Model registry**: models are stored with `metadata.json` for provenance (training data, metrics, date, participant code, band units).

## Communication Flow

1. A band boots. With no Wi-Fi saved it enters setup mode; otherwise it joins your Wi-Fi and opens its sockets.
2. It broadcasts a beacon on UDP 3140 until a computer subscribes (about once a second).
3. The computer (`discovery/manager.py`) hears the beacon, or finds the band by a broadcast `discover` probe, a subnet scan or a typed address, then subscribes over the band's TCP command port (8001) and keeps the subscription alive with a heartbeat.
4. The band reads its sensors at its configured rate (about 59 Hz for a V4 band, 25 Hz for a LASK5) and builds a standard packet.
5. The band sends each frame by UDP to every subscribed computer on port 3141.
6. `UDPListener` receives and `parse_packet()` decodes. The camera and headset labelers arrive over WebSockets (`/ws/webcam`, `/ws/quest`) and are turned into the same kind of packet.
7. While recording, `TemporalMatcher` pairs each band frame with the nearest label frame within the match window.
8. Paired rows are written to a CSV by `CaptureWriter`, with the raw streams in `.sensor.jsonl` and `.label.jsonl` and a `.meta.json` summary that includes the quality verdict.
9. A model is trained on selected CSVs, scored on held-out data, and loaded into the inference engine, which predicts finger movement from live band frames.

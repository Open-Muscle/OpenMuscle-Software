# OpenMuscle Software

OpenMuscle reads the pressure of the muscles in your forearm with a flexible sensor band (the **FlexGrid**), watches your fingers at the same time, and learns the link between the two. Once it has learned, the band alone can predict how your fingers move. Everything runs on your own computer and your recordings stay on it.

This repository holds the software that goes with the bands: a browser app called the **Studio** (live heatmap, recording, training, prediction), the command-line tools behind it, and the firmware libraries. It is open source and part of the [OpenMuscle Hub](https://github.com/Open-Muscle/OpenMuscle-Hub) ecosystem.

## Start here

You have a kit with two FlexGrid bands and a laptop. You do not need to be a programmer.

1. **[Getting started](docs/getting-started.md)**: the whole journey in order, with what you should see at every step (charge, install, start, see the heatmap, record, train, predict).
2. **[Install](docs/getting-started.md#2-install-the-software)**: Windows, Mac and Linux installers; a by-hand fallback is on the same page.
3. **[Put your bands on Wi-Fi](docs/wifi-setup.md)**: what network works, and the step-by-step setup.
4. **[Troubleshooting](docs/troubleshooting.md)**: find your problem by what you see.

More guides:

- [Your data](docs/your-data.md): where recordings are saved, what each file is, what to send back and what to keep private.
- [Band fit and placement](docs/band-fit.md): where the band goes and how to put it back the same way.
- [Studio tour](docs/studio-tour.md): every panel and button, in the order you meet them.
- [First model](docs/first-model.md): a short scripted first recording and training session.
- [Device reference](docs/device-reference.md): the band's buttons, lights and screens on one card.
- [Webcam hand labeling](docs/webcam-labeling.md): using the computer's camera to record finger movement.
- [Glossary](docs/glossary.md): plain-language meanings of the words you will meet.

Want to look around before your bands are on Wi-Fi? Start the Studio, then in a second terminal run the practice kit. It pretends to be two bands and a hand seen by a camera, so you can try record, train and predict with no hardware. Windows: `& "$env:USERPROFILE\OpenMuscle\app\Scripts\openmuscle.exe" simulate --device-type rehearsal --labeler webcam`. Mac and Linux: `~/OpenMuscle/app/bin/openmuscle simulate --device-type rehearsal --labeler webcam`. Press Ctrl+C in that terminal to stop it (see [Command-line tools](docs/pc-cli.md); the full steps are in [Getting started](docs/getting-started.md)).

**Your recordings are private.** `openmuscle web` saves everything you record (takes, sessions, trained models, exports, logs) under `~/OpenMuscle`, outside this repository, and the repository's `.gitignore` also covers those file types if you point `--data-dir` somewhere inside a clone. Recordings are labeled with a participant code, not a name. Nothing is uploaded; share a take only by sending the file yourself.

## What is in the kit

- **Two FlexGrid V4 bands** with 60 pressure sensing cells each. The firmware is already on them; see [FlexGridV4-Firmware](https://github.com/Open-Muscle/FlexGridV4-Firmware).
- **The computer's camera** as the finger labeler. A hand tracker running in your browser turns the picture into finger positions; the picture itself never leaves the browser tab.

Optional extras the software also supports: the **LASK5** finger labeler (four pistons and a joystick; firmware in [LASK5-Firmware](https://github.com/Open-Muscle/LASK5-Firmware)), and a **VR headset** for hand tracking ([VR setup](docs/vr-setup.md)). You can also record with no labeler at all to watch the heatmap, but a model needs a labeler to learn from.

---

# For developers

Everything below is for people who want to build, change or extend the software.

## Repository structure

```
OpenMuscle-Software/
    installers/                 One-click installers: windows/, macos/, linux/
    embedded/
        lib/                    Shared firmware library (om_*.py: settings,
                                network, display, packet, menu, sensor base)
        devices/
            lask5_v2/           LASK5 V2 4-finger labeler + joystick
            openhand_v2/        OpenHand V2 5-finger PCA9685-driven hand
            _template/          Template for new devices
    pc/
        src/openmuscle/         Python package with CLI + web UI
        tests/                  Test suite
        pyproject.toml          Package configuration
    data/
        raw/                    Older sensor captures from earlier firmware
        samples/legacy/         Sample CSVs from the first FlexGrid bands
    docs/                       Documentation
    archive/                    Pre-modular firmware snapshots (preserved
                                verbatim so working state is never lost)
```

**Firmware lives in more than one place, on purpose.** The shipping band firmware is its own repository, [FlexGridV4-Firmware](https://github.com/Open-Muscle/FlexGridV4-Firmware) (flash it with the `flash.py` described in that repository's README and BRINGUP guide). The LASK5 firmware is [LASK5-Firmware](https://github.com/Open-Muscle/LASK5-Firmware). The `embedded/` folder here holds the shared `om_*` library and device examples for building new devices; V4 bands run the FlexGridV4-Firmware repository above, not anything in `embedded/devices/`.

## Install for development

```bash
cd pc/
python -m venv .venv            # then activate it
pip install -e ".[dev]"

pytest -q                       # run the test suite
openmuscle web                  # http://localhost:8000
```

Python 3.10 or newer. `pip install -e .` installs the `openmuscle` command; `python -m openmuscle` works too.

## Command-line tools

```bash
# Browser Studio: live heatmap, recording, training, prediction (this computer only)
openmuscle web

# Check this computer and network, and say what to fix
openmuscle doctor

# Test without hardware: two bands and a labeler that give a learnable dataset
openmuscle simulate --device-type rehearsal

# Without the browser: listen, record paired data, train, predict
openmuscle receive
openmuscle record -o ~/OpenMuscle/captures/my_capture.csv
openmuscle train ~/OpenMuscle/captures/my_capture.csv
openmuscle predict -m ~/OpenMuscle/models/<model folder>/model.pkl
```

| Command | Description |
|---------|-------------|
| `openmuscle web` | Browser Studio. Plain `http` on `127.0.0.1:8000` (this computer only); opens your browser and prints where data is saved. Details and every option: [`docs/pc-cli.md`](docs/pc-cli.md); developer notes: [`pc/src/openmuscle/web/README.md`](pc/src/openmuscle/web/README.md) |
| `openmuscle web --lan` | Same, listening on the whole network so a phone or headset on your Wi-Fi can open it. Anyone on that network can reach it |
| `openmuscle web --vr` | Headset workflow: implies `--lan` and serves `https` only when a mkcert pair is found ([VR setup](docs/vr-setup.md)) |
| `openmuscle web --model M.pkl --hand IP` | Same plus live inference, with optional UDP forwarding of predictions to an OpenHand device |
| `openmuscle doctor [--report]` | Check the computer and network; `--report` writes a text file to send to support |
| `openmuscle firewall` | Windows: show and add the firewall rule for UDP 3140 and 3141 |
| `openmuscle assets fetch` | Download offline copies of the camera hand tracker and the 3D hand viewer |
| `openmuscle bundle <session>` | Pack a session into one zip to send to someone |
| `openmuscle receive` | Live heatmap of sensor data (matplotlib) |
| `openmuscle record -o file.csv` | Record paired data to CSV |
| `openmuscle train data.csv` | Train an ML model (RandomForest) |
| `openmuscle predict -m model.pkl` | Real-time inference (matplotlib) |
| `openmuscle simulate` | Synthetic data for testing; `--device-type rehearsal` is a whole kit |
| `openmuscle models` | List trained models |

## Optional: VR companion (Meta Quest 3)

A WebXR client lets a Quest 3's hand tracking act as the labeler (richer than the LASK5's four pistons) and shows the model's predictions as a ghost hand over your real hand. It is not needed for the kit.

```bash
# Windows one-click launcher: starts the server, sets up adb-reverse,
# opens Quest Browser to /vr automatically
pc/start-vr.bat                 # right arm (default)
pc/start-vr.bat left            # left arm
```

Full setup and per-session walkthrough: [`docs/vr-setup.md`](docs/vr-setup.md).

## Documentation for developers

- [Architecture overview](docs/architecture.md)
- [Packet protocol spec](docs/protocol.md): includes the `quest_hand` device type
- [Data schema](docs/data-schema.md): features, labels and the capture file format
- [Command-line usage guide](docs/pc-cli.md)
- [Adding a new device](docs/adding-a-device.md)
- [VR setup and operation](docs/vr-setup.md) (optional hardware)
- [VR testing scenarios](docs/vr-testing-scenarios.md)
- [Studio source notes](pc/src/openmuscle/web/README.md): web app architecture, REST endpoints, recording and training internals

## Adding a new device

1. Copy `embedded/devices/_template/`
2. Implement your sensor (extends `SensorInterface`)
3. Configure your device (extends `BaseDevice`)
4. Flash to an ESP32 and the PC software auto-discovers it

See [docs/adding-a-device.md](docs/adding-a-device.md) for details.

## Related repositories

**Hardware (KiCad + BOM):**
- [OpenMuscle-FlexGrid](https://github.com/Open-Muscle/OpenMuscle-FlexGrid): 60-sensor flexible pressure-sensing band
- [OpenMuscle-LASK5](https://github.com/Open-Muscle/OpenMuscle-LASK5): 4-finger labeler + joystick (optional ground-truth capture)
- [OpenMuscle-OpenHand](https://github.com/Open-Muscle/OpenMuscle-OpenHand): 5-finger PCA9685-driven robot hand *(repo TBD)*

**Standalone firmware:**
- [FlexGridV4-Firmware](https://github.com/Open-Muscle/FlexGridV4-Firmware): current FlexGrid band firmware (setup mode, discovery, status light and screen)
- [LASK5-Firmware](https://github.com/Open-Muscle/LASK5-Firmware): LASK5 labeler firmware
- [FlexGridV3-Firmware](https://github.com/Open-Muscle/FlexGridV3-Firmware): previous FlexGrid revision, with the sensor-scan techniques writeup

**AR / VR:**
- [OpenMuscle-AR](https://github.com/Open-Muscle/OpenMuscle-AR): AR/VR companion. The current WebXR client lives here in `pc/src/openmuscle/web/static/vr/` (tight coupling to the FastAPI server), but the AR repo is the discoverability anchor and the future home for the planned native Quest APK / BLE-direct work. See its [ROADMAP](https://github.com/Open-Muscle/OpenMuscle-AR/blob/main/ROADMAP.md).

**Coordination / docs:**
- [OpenMuscle-Hub](https://github.com/Open-Muscle/OpenMuscle-Hub): central docs and roadmap

## Contributing

Contributions welcome in Python, MicroPython, hardware design, and documentation.

## License

[MIT](LICENSE)

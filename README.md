# OpenMuscle Software

Software and firmware for the **OpenMuscle** system -- an open-source muscle signal capture platform using flexible pressure-sensing devices.

Part of the [OpenMuscle Hub](https://github.com/Open-Muscle/OpenMuscle-Hub) ecosystem.

---

## Repository Structure

```
OpenMuscle-Software/
    embedded/
        lib/                    Shared firmware library (om_*.py: settings,
                                network, display, packet, menu, sensor base)
        devices/
            flexgrid_v1/        FlexGrid V1 60-sensor pressure band
            lask5_v2/           LASK5 V2 4-finger labeler + joystick
            openhand_v2/        OpenHand V2 5-finger PCA9685-driven hand
            _template/          Template for new devices
    pc/
        src/openmuscle/         Python package with CLI + web UI
        tests/                  Test suite
        pyproject.toml          Package configuration
    data/
        raw/                    Organized sensor captures
        models/                 Trained ML model artifacts
    docs/                       Documentation
    archive/                    Pre-modular firmware snapshots (preserved
                                verbatim so working state is never lost)
```

**Heads up on the firmware split:**

Two patterns coexist on purpose. Modular firmware that shares `embedded/lib/om_*` lives here (active dev cadence, cross-device refactors land atomically). Once a device hits a stable shipping milestone, its firmware is generally **promoted to its own repo** (e.g. [FlexGridV3-Firmware](https://github.com/Open-Muscle/FlexGridV3-Firmware)) so it can version against fixed hardware. The shared lib stays here as the canonical source.

## Quick Start

### PC Tools

```bash
cd pc/
pip install -e .

# Listen for devices with live heatmap
openmuscle receive

# Record paired sensor + label data
openmuscle record -o my_capture.csv

# Train a model
openmuscle train my_capture.csv

# Run real-time predictions
openmuscle predict -m data/models/random_forest_*/model.pkl

# Test without hardware
openmuscle simulate --device-type flexgrid
```

### VR companion (Meta Quest 3)

A WebXR client lets you use a Quest 3's hand tracking as ML ground truth (richer than the LASK5 4-piston labeler) and visualize the model's predictions live as a ghost hand overlaid on your real hand.

```bash
# Windows one-click launcher: starts the server, sets up adb-reverse,
# opens Quest Browser to /vr automatically
pc/start-vr.bat                 # right arm (default)
pc/start-vr.bat left            # left arm

# Or manually: openmuscle web, then in Quest Browser go to
# http://localhost:8000/vr   (via `adb reverse tcp:8000 tcp:8000`)
# https://<lan-ip>:8000/vr   (via `openmuscle web --ssl-certfile cert.pem --ssl-keyfile key.pem`)
```

Full setup + per-session walkthrough: [`docs/vr-setup.md`](docs/vr-setup.md).

### Firmware (ESP32-S3 + MicroPython)

```bash
# Flash MicroPython to ESP32-S3
esptool.py --chip esp32s3 erase_flash
esptool.py --chip esp32s3 write_flash -z 0x0 firmware.bin

# Install libraries
mpremote mip install ssd1306

# Upload shared lib + device firmware
mpremote cp embedded/lib/om_*.py :/lib/
mpremote cp embedded/devices/flexgrid_v1/*.py :/
mpremote mkdir :/config
mpremote cp embedded/devices/flexgrid_v1/config/defaults.json :/config/
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `openmuscle receive` | Live heatmap of sensor data (matplotlib) |
| `openmuscle web` | Browser UI: live heatmap, LASK5 piston bars, ML inference panel, recording, captures management. Also serves the VR companion at `/vr` (see [`docs/vr-setup.md`](docs/vr-setup.md)). Full docs: [`pc/src/openmuscle/web/README.md`](pc/src/openmuscle/web/README.md) |
| `openmuscle web --model M.pkl --hand IP` | Same UI plus live inference, with optional UDP forwarding of predictions to an OpenHand device |
| `openmuscle web --ssl-certfile cert.pem --ssl-keyfile key.pem` | Same UI over HTTPS (required for the VR `/vr` page over LAN, since Quest Browser refuses WebXR hand-tracking on plain HTTP) |
| `openmuscle record -o file.csv` | Record paired data to CSV |
| `openmuscle train data.csv` | Train ML model (RandomForest) |
| `openmuscle predict -m model.pkl` | Real-time inference (matplotlib) |
| `openmuscle simulate` | Synthetic data for testing |
| `openmuscle models` | List trained models |

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Packet Protocol Spec](docs/protocol.md) — includes the `quest_hand` device type
- [Adding a New Device](docs/adding-a-device.md)
- [CLI Usage Guide](docs/pc-cli.md)
- [VR Setup & Operation](docs/vr-setup.md) — mkcert + Quest cert install + per-session walkthrough
- [VR Testing Scenarios](docs/vr-testing-scenarios.md) — bring-up order, 2-minute smoke test, per-feature test runbook

## Adding a New Device

1. Copy `embedded/devices/_template/`
2. Implement your sensor (extends `SensorInterface`)
3. Configure your device (extends `BaseDevice`)
4. Flash to ESP32 -- the PC CLI auto-discovers it

See [docs/adding-a-device.md](docs/adding-a-device.md) for details.

## Related Repositories

**Hardware (KiCad + BOM):**
- [OpenMuscle-FlexGrid](https://github.com/Open-Muscle/OpenMuscle-FlexGrid) — 60-sensor flexible pressure-sensing band
- [OpenMuscle-LASK5](https://github.com/Open-Muscle/OpenMuscle-LASK5) — 4-finger labeler + joystick (ground-truth capture)
- [OpenMuscle-OpenHand](https://github.com/Open-Muscle/OpenMuscle-OpenHand) — 5-finger PCA9685-driven robot hand *(repo TBD)*

**Standalone firmware (promoted from this repo's `embedded/devices/`):**
- [FlexGridV3-Firmware](https://github.com/Open-Muscle/FlexGridV3-Firmware) — current FlexGrid revision, with the sensor-scan techniques writeup

**AR / VR:**
- [OpenMuscle-AR](https://github.com/Open-Muscle/OpenMuscle-AR) — AR/VR companion. The current WebXR client lives here in `pc/src/openmuscle/web/static/vr/` (tight coupling to the FastAPI server), but the AR repo is the discoverability anchor and the future home for the planned native Quest APK / BLE-direct work. See its [ROADMAP](https://github.com/Open-Muscle/OpenMuscle-AR/blob/main/ROADMAP.md).

**Coordination / docs:**
- [OpenMuscle-Hub](https://github.com/Open-Muscle/OpenMuscle-Hub) — central docs and roadmap

## Contributing

Contributions welcome in Python, MicroPython, hardware design, and documentation.

## License

[MIT](LICENSE)

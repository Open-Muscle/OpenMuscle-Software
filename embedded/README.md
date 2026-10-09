# OpenMuscle Embedded Firmware

This folder holds the shared MicroPython library and the example device firmware that build on it. It is for people who want to read, change or extend the firmware.

**If you have a kit and only want to use the bands, you do not need this folder.** The FlexGrid V4 bands already have their firmware. Start with [docs/getting-started.md](../docs/getting-started.md).

## Where the shipping firmware lives

The firmware that runs on the bands and the finger labeler is kept in its own repositories, so it can be versioned against fixed hardware:

| Device | Repository |
|---|---|
| FlexGrid V4 band (the kit) | [FlexGridV4-Firmware](https://github.com/Open-Muscle/FlexGridV4-Firmware): its README and BRINGUP guide explain flashing, setup mode, the status light and screens |
| LASK5 finger labeler (optional) | [LASK5-Firmware](https://github.com/Open-Muscle/LASK5-Firmware) |
| FlexGrid V3 band (previous revision) | [FlexGridV3-Firmware](https://github.com/Open-Muscle/FlexGridV3-Firmware) |

Do not copy `devices/flexgrid_v1/` onto a V4 band. It is an earlier firmware for the first FlexGrid, kept as a worked example of the shared library.

## What is here

```
embedded/
    lib/                        Shared library (om_*.py): settings, network, display,
                                packet building, menu, sensor base class, discovery,
                                provisioning
    devices/
        flexgrid_v1/            Early FlexGrid firmware on the shared library
        lask5_v2/               LASK5 V2 labeler on the shared library
        openhand_v2/            OpenHand V2 robot hand
        _template/              Starting point for a new device
```

Each device folder has a `boot.py`, a main module, and a `config/defaults.json`. The shared library is copied into the device's `/lib` folder.

## Building your own device

Copy `devices/_template/`, implement your sensor, and upload the shared library and your device files with `mpremote`. [docs/adding-a-device.md](../docs/adding-a-device.md) walks through it, and the packet format is in [docs/protocol.md](../docs/protocol.md). The PC software finds devices that follow the protocol automatically.

## Licensing and contributions

All embedded code is open source, licensed under MIT or CERN-OHL-S-2.0. Questions or want to contribute? Visit [openmuscle.org](https://openmuscle.org) or file an issue on GitHub.

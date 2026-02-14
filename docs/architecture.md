# Architecture Overview

## System Diagram

```
                        WiFi / UDP (port 3141)
    +-------------+        +-------------+        +------------------+
    |  FlexGrid   |------->|             |        |   openmuscle     |
    |  (16x4 MUX) |        |   WiFi AP   |------->|   CLI / Package  |
    +-------------+        |             |        |                  |
                           |  "OpenMuscle"|        | receive / record |
    +-------------+        |             |        | train / predict  |
    |   LASK5     |------->|             |        | simulate         |
    |  (4 piston) |        +-------------+        +------------------+
    +-------------+                                      |
         |                                               v
         | ESPNOW (P2P)                          +------------------+
         +-------------------------------------->|   ML Pipeline    |
                                                 |  RandomForest    |
                                                 |  Train/Predict   |
                                                 +------------------+
```

## Directory Structure

```
OpenMuscle-Software/
    embedded/
        lib/                    Shared firmware library (om_*.py)
        devices/
            flexgrid_v1/        FlexGrid production firmware
            lask5_v2/           LASK5 production firmware
            _template/          Template for new devices
    pc/
        src/openmuscle/         Python package
            protocol/           Packet schema + parser
            receiver/           UDP listener + temporal matcher
            data/               CSV storage + dataset loading
            ml/                 Training + inference + model registry
            viz/                Matplotlib visualizations
            simulate/           Virtual sensor transmitter
            cli.py              Click CLI entry point
        tests/                  pytest test suite
        pyproject.toml          Package config
    data/
        raw/                    Organized sensor captures
        models/                 Trained model artifacts
    docs/                       Documentation
    archive/                    Legacy code (preserved for reference)
```

## Key Design Decisions

1. **Shared firmware library** (`embedded/lib/`) - Copied to each device's `/lib` directory.
   All devices inherit from `BaseDevice` and implement `SensorInterface`.

2. **Standard packet protocol** - All devices send JSON over UDP with a versioned schema
   (`v`, `type`, `id`, `ts`, `data`, `meta`). See `docs/protocol.md`.

3. **Backward-compatible parser** - The PC parser auto-detects legacy packet formats
   (bare arrays, Python repr strings) alongside the new protocol.

4. **Single CLI entry point** - All PC operations through `openmuscle` command with
   subcommands instead of scattered standalone scripts.

5. **Model registry** - Models stored with `metadata.json` for provenance tracking
   (training data, metrics, date).

## Communication Flow

1. Device boots, connects to WiFi, opens UDP socket
2. Device reads sensors at configured rate (10 Hz FlexGrid, 25 Hz LASK5)
3. Device builds standard packet via `om_packet.build_packet()`
4. Packet sent over UDP to configured PC IP
5. PC `UDPListener` receives, `parse_packet()` decodes
6. `TemporalMatcher` pairs FlexGrid samples with nearest LASK5 labels
7. Paired data written to CSV via `CaptureWriter`
8. ML model trained on CSV, predictions run in real-time

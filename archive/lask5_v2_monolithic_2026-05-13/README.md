# LASK5 V2 — Monolithic Firmware Snapshot (2026-05-13)

This is a **verbatim filesystem snapshot** of a working LASK5 V2 labeler device pulled off live hardware on 2026-05-13. It captures the **pre-refactor monolithic firmware** that Tory wrote and got working before the `embedded/devices/lask5_v2/` modular refactor (with `om_*` shared lib + `labeler.py` + `sensor_pistons.py`) landed in this repo.

Kept here so the working functionality is preserved verbatim — even if the modular version diverges, regresses, or loses a feature, you can come back to this directory and see exactly what was running.

## Provenance

| Field | Value |
|-------|-------|
| Device | OpenMuscle LASK5 V2 (4-finger labeler + joystick) |
| MCU | ESP32-S3, MicroPython v1.24.1 |
| Source serial | `94A9902F7240CB3F` (whatever was on COM24 that day) |
| Pulled from device on | 2026-05-13 |
| Firmware self-version | `0.2.0`, dated `05-10-2025` (TURFPTAx) per `boot.py` header |
| Modular replacement | [`../../embedded/devices/lask5_v2/`](../../embedded/devices/lask5_v2/) |

## What's here

```
LASK5_Loading_Screen.py     78,538 B  Auto-generated 128×32 OLED splash animation frames (bytearrays)
boot.py                     17,764 B  Main app — settings, network, menu, UDP send mode, calibration. THIS is what was running.
replace_boot.py             16,677 B  Alternate boot file with project-wide default creds (OpenMuscle / 3141592653). Looks like a "factory reset" / shareable variant.
network_manager.py           4,506 B  Wi-Fi STA + UDP send helpers
settings_manager.py          5,538 B  JSON settings load/save with defaults backfill
image_loader.py                211 B  Tiny splash-screen loader stub
bmi160_test.py                 427 B  IMU sensor test (BMI160) — apparently not wired into the main app
wifi_test_boot.py            2,506 B  Standalone Wi-Fi/BSSID scan test for debugging
lib/ssd1306.mpy              1,777 B  Vendored SSD1306 OLED driver (compiled)
settings.example.json          422 B  Template settings — REDACTED Wi-Fi creds, calibration reset to defaults
```

## Redactions

The live `settings.json` and `boot.py` on the device contained the operator's real Wi-Fi SSID and password, and their LAN IP. Before archiving, the following were sanitized so this directory is safe to push to GitHub:

- `boot.py` `defaults` dict: `SSID` and `Pass` replaced with placeholder strings; hardcoded UDP target `'10.0.0.102'` reverted to the original placeholder `'192.168.1.49'`.
- `settings.json` was renamed to `settings.example.json` and its `SSID` / `Pass` / `PCIP` placeholders restored; calibration `mins` / `maxes` reset to the firmware defaults.

To re-deploy this firmware on a fresh device, copy `settings.example.json` → `/settings.json` and fill in the real `SSID`, `Pass`, and `PCIP` values for your network.

## How to restore to a device (if you ever need to)

```powershell
# 1. Erase + flash MicroPython (same image as FlexGrid V3 — ESP32_GENERIC_S3 SPIRAM_OCT)
python -m esptool --chip esp32s3 --port COM<N> erase-flash
python -m esptool --chip esp32s3 --port COM<N> --baud 460800 write-flash -z 0x0 ESP32_GENERIC_S3-SPIRAM_OCT-*.bin

# 2. Copy every file in this directory to the device
mpremote connect COM<N> cp boot.py LASK5_Loading_Screen.py network_manager.py settings_manager.py image_loader.py replace_boot.py wifi_test_boot.py bmi160_test.py :
mpremote connect COM<N> mkdir :lib
mpremote connect COM<N> cp lib/ssd1306.mpy :lib/

# 3. Create your real settings.json from the template
cp settings.example.json settings.json
# edit settings.json: fill in SSID, Pass, PCIP
mpremote connect COM<N> cp settings.json :

# 4. Reset and verify the device boots cleanly
mpremote connect COM<N> reset
```

## What's known about the device's behavior

- Boots into a menu-driven UI rendered on the SSD1306 (128×32) OLED.
- UDP streaming is **not automatic** — operator must navigate the menu to `[0] UDP Send` and activate it. Streams 4 piston ADC values + joystick X/Y at the configured rate to `PCIP:3141`.
- Calibration is a two-step process (release pistons → max, press pistons → min). Values are persisted in `settings.json`.
- Uses ESPNOW peer broadcast in addition to Wi-Fi UDP — peer MAC is `b'\xff' * 6` (broadcast).
- Known typo: line 84 of `boot.py` reads `config['CPIP']` (should be `'PCIP'`). The UDP target IP at line 334 is hardcoded as a workaround, ignoring `settings.json.PCIP`. The modular replacement (`embedded/devices/lask5_v2/`) doesn't have this typo.

## When to use the modular version instead

If you're starting a new LASK5 build, prefer [`embedded/devices/lask5_v2/`](../../embedded/devices/lask5_v2/) — same hardware target, but with the shared `om_*` library, cleaner protocol packets (uniform `{v, type, id, ts, data}` JSON), and proper `BaseDevice` lifecycle. This monolithic archive is here for posterity, not as a starting point.

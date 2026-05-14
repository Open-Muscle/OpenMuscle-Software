# OpenHand V2 — Pre-Modular Firmware Snapshot (2026-05-14)

Verbatim copy of the `boot.py` running on the OpenMuscle hand controller (OM-HAND V2) on 2026-05-14, **before** we patched it to add `auto_mode` boot dispatch + persistent settings.

| Field | Value |
|---|---|
| Device | OpenMuscle hand controller, 5 fingers via PCA9685 |
| MCU | ESP32-S2 |
| Hardware | PCA9685 PWM driver, SSD1306 128×32 OLED, finger servos |
| Pulled from device on | 2026-05-14 |
| COM port | COM23 (USB serial "123456", VID:PID 303a:4001) |
| Modular replacement | [`../../embedded/devices/openhand_v2/`](../../embedded/devices/openhand_v2/) |

## What it does

The original firmware presents a 4-item menu:

1. **ESP-NOW Listen** — listens for ESPNow broadcasts, parses, drives servos
2. **UDP Listen** — connects to WiFi `OpenMuscle`/`3141592653`, listens on port 3145
3. **Servo Test** — wiggles each finger
4. **Release All** — releases all servos

Packet format accepted by `parse_packet()`:

| Format | Example | Routed to device config |
|---|---|---|
| Stringified list | `[354, 556, 446, 664, 1945]` | `default` (sigmoid, reverse, 0–800 → 0–179°) |
| CSV w/ device ID | `PC,30,60,90,120,150` | `PC` (linear, no-reverse, 0–179 → 0–179°) |
| Bare CSV | `30,60,90,120,150` | `default` |

The `'L5'` and `'default'` device configs are sigmoid-shaped (steeper response near 0.5 of input range) and reverse the finger order, suited to LASK5 piston readings. The `'PC'` config is linear passthrough, intended for the future ML-inference pipeline where the PC sends predicted servo angles directly.

## What changed in the modular version

[`embedded/devices/openhand_v2/boot.py`](../../embedded/devices/openhand_v2/boot.py) is a near-verbatim copy with these additions:

1. `settings.json` loaded at boot with `auto_mode` key (`menu` / `espnow` / `udp`).
2. Boot dispatch: if `auto_mode != 'menu'`, jump straight into that receive mode without showing the menu. Pressing Select while in receive mode falls back to the menu.
3. **Escape hatch**: holding the Select button during the very first second of boot skips auto-mode and lands in the menu — useful if a future `auto_mode` value is broken.
4. New menu item `Auto: <current>` that cycles `menu → espnow → udp → menu` and persists on each press.

No protocol or hardware behaviour changed; the packet parser, finger mapping, and `DEVICES` dict are byte-identical.

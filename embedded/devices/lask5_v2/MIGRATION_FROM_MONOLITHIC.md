# Migration Plan — LASK5 monolithic → modular firmware

**Status:** planned, not started. Created 2026-05-13 as a hand-off so the next session can resume cold.
**Target device:** LASK5 V2 currently running monolithic firmware v0.2.0 (Tory's hand-written single-file `boot.py`).
**Migration goal:** Replace the live firmware with this directory's modular version (`labeler.py` + shared `om_*` lib), preserving the features Tory got working, and matching the packet/port conventions used by `openmuscle web` so the new LASK5 panel works out of the box.

---

## 0. Where we left off (context)

- We pulled a verbatim snapshot of the live device firmware into
  [`archive/lask5_v2_monolithic_2026-05-13/`](../../../archive/lask5_v2_monolithic_2026-05-13/).
  That snapshot is the authority on "what works today".
- We patched ONE line of the live device's `boot.py` (line 334) so its
  hardcoded UDP target now points at `10.0.0.102` instead of `192.168.2.103`.
  This was a stop-gap so the existing firmware would talk to the web UI for
  a single session — **do not preserve this hack in the migration**. The
  modular firmware already does this correctly via `settings.json.udp_target_ip`.
- The web UI (`openmuscle web`) now has a **LASK5 Ground Truth** panel
  and an **ML Inference** placeholder panel. Both expect packets in the
  standard OpenMuscle v1.0 format from `om_packet.build_packet()`. The modular
  firmware already produces that format — the monolithic does not.
- LASK5 is on COM24 (MAC `94A9902F7240CB3F`).

---

## 1. Feature gap analysis

Side-by-side of what the live monolithic does vs. what this modular replacement does today.

| Behavior | Monolithic v0.2.0 (live) | Modular (this dir, today) | Migration action |
|---|---|---|---|
| **Splash / loading animation** | 78 KB of frame bytearrays in `LASK5_Loading_Screen.py`, played at boot | none | Port frames to `display_lask5.py` as an optional `play_splash(display)` helper. Keep frame data in a separate module to keep `labeler.py` small. |
| **On-device menu UI** | Multi-screen menu navigated by joystick + buttons. UDP send is `[0] UDP Send` mode | no menu — auto-starts streaming | Use `om_menu.py` from the shared lib. Modes: `Live UDP` (default, auto-enters at boot), `Calibrate`, `Settings`, `About`. Keep it minimal. |
| **UDP streaming gating** | Menu-gated (only streams in UDP Send mode) | Continuous, starts at boot | **Decision: continuous.** Matches FlexGrid behavior and makes the web UI "plug-and-play". The menu can still have a "stop streaming" toggle for power saving. |
| **UDP target port** | `3141` (hardcoded at line 334; same as FlexGrid) | `3145` (in `DEVICE_DEFAULTS`) | **Change modular default to 3141** so a stock LASK5 talks to `openmuscle web` without reconfiguration. |
| **UDP target IP** | Hardcoded at line 334 (we patched), ignores `settings.PCIP` | Reads `settings.udp_target_ip` correctly | No action — modular is already correct. |
| **Wi-Fi creds source** | `settings.json` keys `SSID` / `Pass` | `settings.json` keys `wifi_ssid` / `wifi_password` | Key names differ — see §3 settings migration. |
| **Calibration flow** | Two-step interactive (`release` → max, `press` → min), saved to `settings.json` | None on-device — relies on values already in `settings.json` | Port the calibration UX. Implement as a menu item that prompts on the OLED, reads ADCs after a 2 s delay, persists via `om_settings`. |
| **IMU support** | `bmi160_test.py` standalone (not wired into main app on live device) | None | Defer. Out of scope for this migration. Note in the new README. |
| **Joystick** | Read in fastReadLoop, sent in UDP packets | Read in `_send_loop`, sent as `data.joystick = {x, y}` ✓ | No action — modular already does this. |
| **ESPNOW peer broadcast** | Yes, peer = `b'\xff' * 6` | Yes, same peer (`init_espnow()` called in `run()`) ✓ | No action. |
| **Packet format** | Raw / app-specific (older format) | Standard v1.0 (`{"v":"1.0","type":"lask5",...}` via `om_packet.build_packet`) | Web UI relies on standard format — **no action**, modular is correct. |
| **LED status indicator** | `ledPIN: 8` in defaults, `ledPIN: 15` in live settings | `led_pin: 15`, 2-blink boot indicator | No action — modular matches the live override. |

---

## 2. Decisions baked into the new firmware

These are the bits where the monolithic and modular diverge and we need to pick a behavior. Recording the rationale here so the next session doesn't re-litigate them.

1. **Continuous UDP streaming, not menu-gated.** Eliminates the "device looks dead in the web UI until the operator presses a menu button" failure mode. Power impact is negligible at 25 Hz.
2. **UDP port 3141.** Same port as FlexGrid. Means `openmuscle web` doesn't need to listen on multiple ports, and any future device can default to 3141 too.
3. **Settings key renames.** `SSID`→`wifi_ssid`, `Pass`→`wifi_password`, `PCIP`→`udp_target_ip`, `sclPIN`→`scl_pin`, etc. (the modular naming convention). Provide a one-shot migration script in `tools/` or have the boot code accept either set with a deprecation warning.
4. **Calibration UX preserved.** Tory uses it, we keep it. Implement as the first non-Live menu item.
5. **Splash optional.** Show it at boot if `display_lask5.py:play_splash` succeeds; if the module/frame data isn't on the device, just skip silently. Don't make boot fail if the splash file is missing.
6. **`replace_boot.py` archive-only.** Don't bring this into the modular tree — it was a "factory reset to project-wide defaults" trick that's now naturally covered by `config/defaults.json`.

---

## 3. Settings.json migration

The live device's `settings.json` uses snake_case-with-mixed-caps keys (`SSID`, `Pass`, `sdaPIN`, `joystick_yPIN`, `PCIP`). The modular `om_settings` expects snake_case-lowercase (`wifi_ssid`, `wifi_password`, `sda_pin`, `joystick_y_pin`, `udp_target_ip`).

Mapping table (live → modular):

| Live key | Live value | Modular key | Notes |
|---|---|---|---|
| `SSID` | (operator's network) | `wifi_ssid` | |
| `Pass` | (operator's password) | `wifi_password` | |
| `PCIP` | `10.0.0.102` | `udp_target_ip` | |
| — | — | `udp_port` | New, set to `3141` |
| `device_name` | `"OpenMuscle Labeler"` | `device_id` | `"lask5-01"` or similar |
| `sclPIN` | `9` | `scl_pin` | |
| `sdaPIN` | `8` | `sda_pin` | |
| `oledWIDTH` | `128` | `oled_width` | |
| `oledHEIGHT` | `32` | `oled_height` | |
| `ledPIN` | `15` | `led_pin` | |
| `startPIN` | `11` | `start_pin` | |
| `selectPIN` | `10` | `select_pin` | |
| `upPIN` | `41` | `up_pin` | |
| `downPIN` | `42` | `down_pin` | |
| `joystick_xPIN` | `6` | `joystick_x_pin` | |
| `joystick_yPIN` | `5` | `joystick_y_pin` | |
| `Joystick_SW` | `7` | `joystick_sw_pin` | |
| `mins` | `[1689, 1401, 1399, 1131]` | `mins` | Preserve operator's calibration |
| `maxes` | `[1943, 1879, 1863, 1929]` | `maxes` | Preserve operator's calibration |
| `sensor_mapping` | `false` | — | Unused, drop |
| `device_mac` | `false` | — | Unused, drop |
| `led` | `false` | — | Unused, drop |

Note: the operator's `mins`/`maxes` from the live device should be preserved on first deploy. The redacted `archive/lask5_v2_monolithic_2026-05-13/settings.example.json` has them reset to defaults; the live device still has the real values in its `/settings.json` until we wipe it.

---

## 4. Migration steps (in order)

Each step is intended to be done with the device on COM24 and an mpremote session. Each is reversible by re-flashing the archived monolithic snapshot.

1. **Pre-flight: re-read the live device's `settings.json`** (with real creds + calibration). Save to host locally. This is the *only* thing we can't recreate from the repo.
2. **In this repo: bump `DEVICE_DEFAULTS.udp_port` from `3145` → `3141`** in `labeler.py`. Update `config/defaults.json` to match.
3. **In this repo: implement settings key migration in `om_settings`** — if a legacy key (`SSID`, `Pass`, `PCIP`, `*PIN`) is seen on load, rewrite to the modern name and persist. One-shot migration; harmless if already migrated.
4. **In this repo: implement `display_lask5.play_splash(display)`** — load frame data from `splash_frames.py` (port from `LASK5_Loading_Screen.py`), play once with `time.sleep_ms` pacing. Make the import optional.
5. **In this repo: implement `Calibrate` menu item** using `om_menu` — two prompts ("release pistons", "press pistons"), 2 s delay each, persist via `settings.save()`.
6. **In this repo: wire the menu into `labeler.run()`** — first item is `Live UDP` (auto-entered if no button pressed within 2 s), then `Calibrate`, `Settings`, `About`. Streaming runs in the background regardless of menu state.
7. **Test in a host harness if practical** — `om_packet.build_packet` should produce JSON identical-in-shape to what the web UI expects. Easy unit-testable.
8. **Deploy to device:**
   ```
   mpremote connect COM24 mip install ssd1306
   mpremote connect COM24 cp ../../lib/om_*.py :/lib/
   mpremote connect COM24 cp boot.py labeler.py sensor_pistons.py display_lask5.py splash_frames.py :
   mpremote connect COM24 mkdir :config
   # Write a freshly-merged settings.json from the pre-flight read in step 1
   mpremote connect COM24 cp config/settings.json :/config/
   mpremote connect COM24 reset
   ```
9. **Verify** with the checklist below.

---

## 5. Verification checklist

After deploy, in order:

- [ ] Device boots cleanly (no traceback on serial console).
- [ ] OLED shows splash, then the LASK5 taskbar.
- [ ] Wi-Fi connects within 20 s (`om_logger` prints IP).
- [ ] LASK5 appears in `openmuscle web` Devices panel within 5 s of boot.
- [ ] All 4 piston bars in the **LASK5 Ground Truth** panel respond to physical piston presses.
- [ ] Joystick canvas shows the dot moving when the joystick is wiggled.
- [ ] Calibration menu actually persists `mins`/`maxes` (verify by power-cycling and reading `/settings.json` back).
- [ ] On-device menu navigates with the up/down buttons and selects with `start`.
- [ ] `openmuscle record` produces a CSV that includes matched FlexGrid + LASK5 rows (via the existing `TemporalMatcher`).

---

## 6. Out of scope (defer to a follow-on)

- IMU integration (`bmi160_test.py` is just a sketch).
- Battery monitoring (live device has `ADC_BAT`-style hardware but no firmware support).
- ESPNOW comms with peer FlexGrids (the modular code initializes it but nothing uses it yet).
- Per-device unique `device_id` provisioning — current default is `"lask5-01"` for all devices; we'll want to ID per MAC eventually.
- Live ML inference panel — the web UI already has the empty slot; wiring up actual inference (model load, FlexGrid frame → predicted pistons) is a separate task.

---

## 7. Rollback

If migration breaks something on the live device:

```
cd ../../archive/lask5_v2_monolithic_2026-05-13
mpremote connect COM24 cp *.py :
mpremote connect COM24 cp lib/ssd1306.mpy :lib/
# Restore the real settings.json (with operator's creds + calibration)
mpremote connect COM24 cp <saved-pre-flight-settings.json> :/settings.json
mpremote connect COM24 reset
```

The archive snapshot is byte-identical to what was running on 2026-05-13, so a rollback yields the exact known-working state.

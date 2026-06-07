# VR Testing Scenarios + Smoke-Test Checklist

A practical runbook for validating the OpenMuscle VR app against the hardware. Use this when you sit down to test: it gives you a fixed bring-up order, a 2-minute smoke test to confirm the basics, and per-feature scenarios with expected results so you can validate each piece fast instead of rediscovering how it all works.

Companion docs:
- [`vr-setup.md`](vr-setup.md) -- one-time setup (mkcert, Quest cert install, launchers)
- [OpenMuscle-AR wiki](https://github.com/Open-Muscle/OpenMuscle-AR/wiki) -- troubleshooting + community successes log
- [`pc/src/openmuscle/web/README.md`](../pc/src/openmuscle/web/README.md) -- architecture of the web/VR surface

---

## Bring-up order (do this every session, in order)

Order matters. Each step depends on the previous one being live.

1. **Power the FlexGrid.** Wait for its OLED to show it joined Wi-Fi. It streams UDP to the PC on port 3141.
2. **Start the server on the PC.** Pick the path that matches how you'll test:
   - **USB tethered** (fast iteration, no certs): `cd pc` then `start-vr.bat` (VR) or `start-vr.bat right ar` (AR). It auto-runs `adb reverse` and opens Quest Browser for you.
   - **Cordless HTTPS** (walk around, real field capture): `cd pc` then `start-vr-https.bat`. It prints the headset URL. Requires the one-time mkcert + Quest cert install.
3. **Confirm the device shows up.** On the PC, open the desktop UI (`http://localhost:8000/` or `https://<ip>:8000/`). The Devices panel should list `flexgrid-v3-*` with a non-zero Hz and a live heatmap.
4. **Put the headset on. Set both controllers down** on a flat surface. Quest auto-switches to hand tracking within 2 to 5 seconds. (It stays in controller mode as long as it can see them being held.)
5. **Open `/vr` in Quest Browser** (the USB launcher does this for you; for HTTPS, type the printed URL). Confirm the three preflight checkmarks are green, then tap Enter VR / Start AR.

If any step fails, the [troubleshooting wiki page](https://github.com/Open-Muscle/OpenMuscle-AR/wiki/Troubleshooting) lists the common causes per symptom.

---

## 2-minute smoke test (no FlexGrid required)

The fastest "is anything obviously broken" check. Only needs the headset + PC + server. Do this first whenever you pick the project back up after code changes.

| # | Action | Expected |
|---|---|---|
| 1 | Open `/vr` (or `/vr?mode=ar`), look at the preflight page | Three green checkmarks: secure context, WebXR supported, server reachable |
| 2 | Tap Enter VR / Start AR | Scene loads. VR = dark background; AR = passthrough (you see your room) |
| 3 | Look forward | Heatmap panel (says "waiting for FlexGrid"), 3x2 menu below it, status strip below that |
| 4 | Hold up your captured arm (the FlexGrid arm) | Blue joint spheres on that hand |
| 5 | Hold up your other hand | Green joint spheres + a blue ray pointer from the index |
| 6 | Point the ray at a menu button | Ray turns amber, button highlights |
| 7 | Pinch (off-hand index + thumb) while hovering RECENTER | Brief white flash on the button; panels re-anchor in front of you |
| 8 | Point at a panel's drag handle (small cube, top-left corner) and pinch-hold | Handle turns green; panel follows your hand. Release: panel drops + faces you |
| 9 | Tap REC | Yellow SYNC slate flashes ~2.5s; menu collapses to one big red STOP button; header shows a live ms clock + filename |
| 10 | Tap STOP | Menu returns to the full 3x2 grid; status strip shows "saved: ..." |

If all 10 pass, the core interaction loop is healthy. If FlexGrid is connected, the heatmap in step 3 animates and the REC in step 9 actually pairs sensor frames.

---

## Full test scenarios

Each scenario is independent. Run the ones relevant to what you changed.

### Scenario A: Gesture-training capture (VR mode)

The deliberate-gesture workflow for building training data.

1. Launch VR mode: `start-vr.bat right` (or left). Enter VR.
2. Tap **SESSION** (turns red). All captures now group under one session.
3. Tap **REC**. Curl your index finger open/close ~10 to 15 times. Tap **STOP**.
4. Repeat step 3 for middle, ring, pinky. (Thumb is intentionally excluded -- FlexGrid can't see it.)
5. Tap **SESSION** again to end it.
6. Tap **TRAIN**. Wait ~10 to 30s.

**Expected:**
- Each REC shows a rising row count + match rate in the header while recording.
- Status strip after each STOP: `saved: capture_<ts>.csv (N rows, match X%)`. Match rate should be well above 0 if both FlexGrid and Quest are streaming.
- TRAIN status: `trained: R2=<value> ... model loaded ... predict ON`.
- On the PC, `pc/data/raw/merged/` has the capture CSVs + `.labels.schema.json` + `.meta.json` sidecars, and `pc/data/raw/sessions/` has the session JSON.

**Watch for:** match rate near 0 (Quest WebSocket not connected, or hands out of camera FOV during capture). Very low R2 is normal for a first small dataset; record more and retrain.

### Scenario B: Field capture (AR passthrough mode)

The natural-activity workflow. You see your real workspace and do real tasks.

1. Launch AR mode: `start-vr-https.bat`, open `https://<ip>:8000/vr?mode=ar` (cordless), or `start-vr.bat right ar` (tethered for a quick check). Start AR.
2. Confirm passthrough: you should see your real room behind the panels. (If it is black, the AR session did not grant passthrough -- check the browser console for the blend-mode log line.)
3. **Start the Quest's built-in screen recorder** (press and hold the Meta button, or use the camera shortcut) so the video is captured for later labeling.
4. Drag the panels out of your central view using the handles, so they sit in your periphery and do not block your work.
5. Tap **REC**. Do a real task with your hands in view (type, stir, assemble something). Tap **STOP** when done.
6. Stop the screen recorder.

**Expected:**
- SYNC slate flashes at REC with `capture_<ts>.csv` + a Unix-ms timestamp. This is your video-to-CSV pairing anchor.
- Header strip shows a live `HH:MM:SS.mmm` clock + filename throughout the recording, visible in the screen recording.
- Captured-arm joints stream to the CSV whenever the hand is in the headset cameras' FOV; frames where the hand is out of view are dropped (gaps in the data, by design).

**Watch for:** panels snapping back to center after you moved them -- if that happens on reload, the localStorage layout persistence regressed (see Scenario E).

### Scenario C: Pause / boundary redraw recovery

This is the v1.11 fix. It used to glitch the app.

1. Enter either mode. Tap **REC** to start a recording.
2. Trigger a pause: press the **Meta button** (opens the universal menu) OR walk past your Guardian boundary so Quest prompts a redraw.
3. Observe the status strip, then close the menu / finish the redraw.

**Expected:**
- During pause: status strip reads `session paused (visible-blurred) -- boundary redraw or system UI`. No joint frames are sent during this time (the recorder timeline stays clean).
- On resume: status reads `session resumed after Xs -- re-anchoring UI`, and the panels pop back in front of your current head position.
- If you were mid-drag when the pause hit, the panel is released cleanly (not stuck following a frozen controller).
- Tap **STOP**: the recording closes normally with all the valid pre/post-pause frames.

### Scenario D: Collapse-to-STOP while recording

1. Enter either mode. Confirm the full 3x2 menu is visible.
2. Tap **REC**.

**Expected:** the menu collapses to a single large red STOP button at the same location. The status strip stays visible. Pointing + pinching STOP ends the recording and the full menu returns.

**Why it matters:** during real field capture you do not want six action buttons cluttering your view -- just an obvious way to stop.

### Scenario E: Panel layout persistence

This is the v1.12 feature.

1. Enter a mode. Drag the heatmap cluster and the menu to custom positions.
2. Exit VR (or just reload the Quest Browser tab) and re-enter the same mode.

**Expected:** the panels come back where you left them. VR and AR remember their layouts independently (per-mode localStorage key).

3. Now tap **RECENTER**.

**Expected:** panels reset to defaults AND the saved layout is cleared, so a subsequent reload also starts from defaults until you drag again.

### Scenario F: Predict + ghost hand

1. Complete Scenario A at least once so a model is trained and loaded.
2. Confirm **PREDICT** is red (TRAIN auto-enables it; otherwise tap it).
3. Hold your captured hand up in view and curl fingers.

**Expected:**
- The REAL-vs-PRED bar panel appears (four fingers, green = real curl, amber = predicted).
- An amber ghost hand overlays your real hand, anchored to your real wrist position + orientation, showing what the model predicts.
- As the model improves with more training data, the ghost should track your real hand more closely.

---

## What "good" looks like (acceptance bar)

Before calling the app "ready to demo or hand to a tester," these should all be true:

- [ ] 2-minute smoke test passes end to end
- [ ] Scenario A produces a trainable CSV with match rate > 50%
- [ ] Scenario B passthrough is visible and the sync slate + header clock are legible in the screen recording
- [ ] Scenario C recovers gracefully from a Meta-button pause with no app glitch
- [ ] Scenario D collapses and restores the menu correctly
- [ ] Scenario E remembers panel layout across a reload and RECENTER clears it
- [ ] Scenario F shows the ghost hand tracking once a model is trained

---

## Reporting a problem

If a scenario fails, capture:
- Which scenario + step
- Mode (vr / ar) and transport (USB / HTTPS)
- Quest model + Horizon OS version
- Browser console output (via `chrome://inspect/#devices` with the Quest USB-connected) and the `openmuscle web` server console

File it as an issue on [OpenMuscle-AR](https://github.com/Open-Muscle/OpenMuscle-AR/issues) (AR/VR-specific) or [OpenMuscle-Software](https://github.com/Open-Muscle/OpenMuscle-Software/issues) (server/pipeline). Add a [Troubleshooting wiki](https://github.com/Open-Muscle/OpenMuscle-AR/wiki/Troubleshooting) entry once you resolve it so the next person benefits.

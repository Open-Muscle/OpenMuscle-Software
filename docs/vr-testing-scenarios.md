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

## Zero-hardware smoke test (no headset, no FlexGrid)

Before any headset session, you can exercise the entire PC-side quest
pipeline with the simulator. Two terminals:

```bash
cd pc
openmuscle web                              # terminal 1
openmuscle simulate --device-type combo     # terminal 2
```

Then in the desktop UI (`http://localhost:8000/`):

1. Devices panel lists `flexgrid-sim` (~12 Hz, live heatmap) and
   `quest-sim` (~25 Hz, `quest_hand`).
2. The Live stage swaps the piston comparator for the 3D hand viewer; a
   green articulated hand auto-rotates and its fingers visibly curl.
3. REC pairs frames (match rate goes green), STOP writes a CSV with 64
   sensor columns and 175 label columns plus the labels-schema sidecar.
4. Train on that capture; the combo simulator's sensor values are
   derived from the same latent finger curls as the hand, so the model
   should reach a clearly positive held-out R^2. Load the model and the
   amber predicted hand tracks the green real hand in the viewer.

If all of that works, a real-headset failure is in the headset/network
layer, not the PC pipeline.

The two-hand per-hand path (Scenario G) also has an automated end-to-end test
that records a synthetic bilateral session, trains a model per hand via
`--role`, and asserts each band predicts through its own model -- run
`cd pc && python -m pytest tests/test_two_hand_pipeline.py` to confirm the
record -> train -> infer wiring before you bring up hardware.

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
| 9 | Tap REC | Yellow SYNC slate flashes ~2.5s; menu collapses to one big red STOP button; header shows a live ms clock + filename + a capture-quality dot (gray while warming up) |
| 10 | Tap STOP | Menu returns to the full 3x2 grid; status strip shows "saved: ..." |

If all 10 pass, the core interaction loop is healthy. If FlexGrid is connected, the heatmap in step 3 animates and the REC in step 9 actually pairs sensor frames.

### Reading capture quality (live, while recording)

The recording header is also a live data-quality gauge, so you can tell mid-capture whether the data is good without taking the headset off:

- **Quality dot color** (tracks the sensor-to-label match rate):
  - **gray** = warming up (fewer than ~10 sensor frames seen yet)
  - **green** = match rate >= 70% (good)
  - **amber** = 40 to 70% (marginal -- check that both FlexGrid and Quest are streaming)
  - **red** = below 40% (poor pairing -- something is off; see Troubleshooting)
- **`NN%`** in the header is the live match rate.
- **`JOINTS DROPPING`** appears (and the dot goes amber) when your hand is partially out of the cameras' view and the headset is sending incomplete joint frames. Those frames get zero-filled joint columns -- keep your capture hand more fully in view if you see this a lot. The final count is also reported in the stop result (`label_width_mismatch`).

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

### Scenario G: Two-hand bilateral capture + per-hand models

The two-band workflow: capture BOTH arms at once, train one model per hand, and see a ghost predict next to each real hand. This is the push-button path for the first real two-hand test. Each hand gets its OWN model (mirror musculature + different per-arm placement mean one model cannot predict both arms).

**Extra bring-up:** power BOTH FlexGrid bands (one per forearm) and wait for each to join Wi-Fi. If a band does not appear in the Devices/Sources panel (DHCP moved its IP, or its beacon is suppressed because another hub holds it), use the Sources panel **Scan subnet** to find it.

1. **Start the server.** `cd pc` then `openmuscle web`. It auto-loads the mkcert TLS pair from `~/.openmuscle/` (see [`vr-setup.md`](vr-setup.md)) and prints the headset URL.
2. **Tag the bands.** In the PC desktop UI Sources panel, tag one band **left** and the other **right** (tag the LASK5/labeler too if you use one). The tag is which forearm each band is physically on.
3. **Verify the tags BEFORE recording (the one step that matters most).** Squeeze each band in turn and watch the in-headset status panel + heatmaps: the band you squeeze should be the one whose heatmap lights up, on the side you expect. Both bands should show battery + a non-zero Hz + a green signal dot, and each shows an XYZ orientation gizmo that tilts as you rotate that forearm. If left/right are swapped here, re-tag now -- a swap trains the models backwards.
4. **Open two-hand AR in the headset:** `/vr?mode=ar&arm=both`, or tap the **AR / Two-hand** preset button on the landing page (no typing in the headset). Hold up BOTH hands: you should see a heatmap per band side by side and joint spheres on both hands.
5. **Record.** Tap **REC** (or "Record two-hand"), do the chore/gesture with both hands in view, tap **STOP**. One CSV is written with both bands' rows (role-tagged left/right); each band's rows carry ITS hand's labels + `forearm_roll_deg`/`palm_up`, and the per-device IMU `scale_dict` lands in the `.meta.json`.
6. **Train one model per hand** from that single CSV:
   ```bash
   cd pc
   openmuscle train data/raw/merged/<capture>.csv --role left  -o model_left.pkl
   openmuscle train data/raw/merged/<capture>.csv --role right -o model_right.pkl
   ```
7. **Restart the server with both models:**
   ```bash
   openmuscle web --model-left model_left.pkl --model-right model_right.pkl
   ```
8. **Re-open** `/vr?mode=ar&arm=both`, confirm PREDICT is on, hold up both hands and curl fingers.

**Expected:**
- Step 3: squeezing the LEFT band lights the LEFT heatmap (and right the right).
- Step 5: STOP shows the row count; the CSV has a `role` column with both `left` and `right` rows, plus `forearm_roll_deg`/`palm_up`, and `auto.imu_scale` in the `.meta.json`.
- Step 6: each run prints `Role filter: <side> -> N rows, single-arm model` and saves its own `.pkl`.
- Step 8: an amber ghost hand overlays EACH real hand, each driven by ITS hand's model (left ghost from model_left, right from model_right). Curling one hand moves only its own ghost.

**Watch for:**
- **Only one heatmap / one ghost:** only one band is tagged or streaming. Check both are tagged left/right in Sources and both show Hz > 0.
- **No ghost at all:** the model grid must match the band (a 15x4 band = 60 features; a model trained on a different grid will not load against it). The inference status shows the feature-count mismatch.
- **Ghosts swapped / mirrored:** left/right were swapped at capture (step 3), so each model trained on the wrong arm. Re-tag and re-capture.

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
- [ ] Scenario G captures both arms, trains a model per hand, and shows a ghost per real hand driven by its own model

---

## Reporting a problem

If a scenario fails, capture:
- Which scenario + step
- Mode (vr / ar) and transport (USB / HTTPS)
- Quest model + Horizon OS version
- Browser console output (via `chrome://inspect/#devices` with the Quest USB-connected) and the `openmuscle web` server console

File it as an issue on [OpenMuscle-AR](https://github.com/Open-Muscle/OpenMuscle-AR/issues) (AR/VR-specific) or [OpenMuscle-Software](https://github.com/Open-Muscle/OpenMuscle-Software/issues) (server/pipeline). Add a [Troubleshooting wiki](https://github.com/Open-Muscle/OpenMuscle-AR/wiki/Troubleshooting) entry once you resolve it so the next person benefits.

# Webcam hand labeling

Use the computer's camera to record how your fingers move while the bands record
how your forearm feels. The camera watches a hand, a hand tracker in the browser
turns the picture into joint positions, and those are saved next to the band data.
This is how a recording gets the finger information a model learns from, with no
extra hardware.

For the starter kit the camera is the labeler: it is the way to get a trained model
without extra hardware. You can also train from a VR headset or the optional LASK5
finger labeler if you have one. Recording with no labeler at all works too, but a
take without finger data cannot be used to train a model; see
[No camera](#no-camera-recording-bands-only).

## Your first labelled recording

You need the computer that has the camera, with `openmuscle web` running and at
least one band showing in the dashboard.

1. Open the dashboard at **http://localhost:8000**. `openmuscle web` prints the
   address it is using in its first lines and opens it for you; if port 8000 was
   busy it picked the next free one, so use the address it printed. (The address
   starts with `https` only if you set up the headset certificate described in
   [vr-setup.md](vr-setup.md).)
2. Find the **Camera** panel near the top of the page and open it. Press **Start
   camera** inside it. The first time, your browser asks whether this page may use
   the camera; choose **Allow**.
3. Hold one hand up to the camera, palm toward it, all fingers visible, about an
   arm's length away. A green skeleton is drawn over the hand and the five
   finger-curl bars move. Those bars are the numbers that get recorded.
4. In the bar at the top, make sure **Finger data from** says **Webcam**
   (choosing it opens the Camera panel for you). The line beside the Record
   button should read "Ready ... Press Record."
5. Press **Record**, move your fingers (open, close, curl one at a time), and press
   it again to stop. The new take appears in the Captures list.

Keep the dashboard tab in front while you record. The Camera panel is inside the
dashboard so that one visible tab does both jobs. If you close the panel the
camera keeps running; you do not need to leave it open.

### If you would rather use a separate window

The camera page also works on its own at **http://localhost:8000/webcam** (use the
same port as the dashboard). Browsers pause a tab that is in the background, and
the hand labels stop with it. So if you use the separate page, put it side by side
with the dashboard, not behind it. The page warns you when it was hidden, and the
dashboard says the camera has stopped sending hand data.

## Working with no internet

The first time the camera starts it downloads the hand tracker (about 17.6 MB)
from the internet, and the browser keeps it. To keep a copy on this computer so
the camera also works with no internet, connect once and run:

```
openmuscle assets fetch
```

That saves the tracker and the 3D hand viewer under `~/OpenMuscle/assets`, checks
every file against a checksum built into OpenMuscle, and throws away any file that
does not match. Restart `openmuscle web`, reload the page, and the camera panel
says **tracker: ready (local)**. `openmuscle assets status` shows what is
installed. Add `--with-nosimd` only if you have a very old processor. For a kit
without internet, copy the `assets` folder from another computer into the same
place; `assets status` tells you if every file is present and correct.

Where the files come from and their licences are written to `NOTICE.txt` in that
folder: MediaPipe tasks-vision 0.10.14 (Apache-2.0), the MediaPipe Hand Landmarker
model published by Google, and three.js 0.160.0 (MIT).

## No camera: recording bands only

If you have no camera, or only want to watch and save what the bands feel, set
**Finger data from** to **No labeler (bands only; cannot be used to train)**. The
line beside the Record button then reads:

> Ready: 2 bands (left arm, right arm), no finger data (cannot be used to train). Press Record.

Press **Record** and **Stop** as usual. The take is saved with its band readings,
and it is tagged **no labels** with the word **Check**, because there are no finger
positions in it. That is expected, not a fault.

What a bands-only take is good for: watching the heatmaps, checking the fit of the
bands, practising the routine, keeping the readings, and exporting them with
**Export session**. What it cannot do: **Train** will not use it. If you try, the
card **Could not train yet** says the recordings have no finger labels and to record
again with the webcam or finger labeler turned on.

Finger positions cannot be added to a take afterwards, so to get a model, record
again with a camera (any laptop camera or USB webcam will do), a LASK5 or a headset.
The camera is only needed while recording the takes you train from. Once a model
exists, live prediction runs from the bands alone.

## Getting good labels

- Palm facing the camera, all five fingers visible, about an arm's length away.
- Bright, even light. A plain background helps.
- Keep the hand in frame. When tracking is lost no labels are sent, so the
  capture simply has a gap rather than wrong values.
- Curling fingers straight toward the camera is the hardest motion for a single
  camera to measure. Turning the hand slightly so the curl is seen from the side
  improves it.
- The page decides left or right from what the camera sees and keeps that decision
  steady, so one bad frame does not change it. The hand being recorded is drawn
  in bright green; any other hand in view is drawn faint and ignored.
- If it keeps calling your left hand "right" (some external cameras and
  phone-as-webcam apps flip the picture), tick **Swap left and right**. The choice
  is remembered. Or pick the hand yourself under **Hand in view**.

## If something goes wrong

The camera panel says in plain words what happened. The usual cases:

| What you see | What it means | What to do |
|---|---|---|
| "The browser was not allowed to use the camera" | You chose Block, or the system blocks the browser | Click the camera icon in the address bar and choose Allow. On Windows also check Settings > Privacy & security > Camera; on a Mac, System Settings > Privacy & Security > Camera. |
| "The camera is busy or could not be opened" | Another program or tab has it | Close Zoom, Teams, FaceTime, the Camera app, or other tabs using the camera, then Start camera again. |
| "No camera was found" | Nothing plugged in, or switched off | Plug in or switch on a webcam, check the system lists it, then Start camera again. |
| "Could not download the hand tracker, check internet access" | The tracker comes from the internet and there is none | Connect once and run `openmuscle assets fetch`, or check the connection. |
| "Graphics acceleration did not work here" (a note, not an error) | The graphics card cannot run the tracker | Nothing. It switched to the processor automatically; it works but may track fewer frames per second. |
| "The hand tracker stopped working" | It failed again after switching to the processor | Press Start camera. If it repeats, close other programs that use the camera or graphics card and reload. |
| "The camera only works when this page is opened on the computer that has the camera" (in the dashboard Camera panel) or "The browser blocks the camera on this address" (on the `/webcam` page) | The page was opened by another address, such as the computer's network address | On the computer with the camera, open the `http://localhost:8000` address. |
| "That camera cannot give the picture size this page asks for" | The chosen camera does not offer the picture size the page asks for | In the **Camera** list choose **default camera**, then press **Start camera**. |
| "The hand tracker could not start in this browser" | The browser cannot run the tracker | Use the current Chrome or Edge, then press **Start camera** again. |
| "Cannot reach the OpenMuscle server" | The page cannot connect, or it was opened from another device | Check `openmuscle web` is still running; open the page on the same computer. |
| Hand skeleton on the wrong side | Some cameras mirror the picture | Tick **Swap left and right**. |
| "Tracking was paused ... window in the background" | The tab was hidden, so no labels were sent | Keep the window visible; use the Camera panel in the dashboard. |

Camera labels can only come from the computer running OpenMuscle: the labeler
socket refuses connections from any other device, so another computer on your
network cannot add or change labels in a recording.

## Browser and computer support

The camera page needs a current browser on the computer that has the camera, opened
at `http://localhost:8000` (or the page on its own at `http://localhost:8000/webcam`).
It uses three things every current browser has: the camera, WebAssembly (to run the
hand tracker) and modern scripts. The table says what has actually been checked.

| | Windows | macOS | Linux |
|---|---|---|---|
| **Chrome** | Built for it; partly checked | Expected to work | Expected to work |
| **Edge** | Expected to work | Expected to work | Expected to work |
| **Firefox** | Untested | Untested | Untested |
| **Safari** | Not available | Untested | Not available |

What the words mean:

- **Built for it; partly checked.** The page logic and the server are tested by
  automatic checks, and the full flow (camera panel, Start, a fallback from graphics
  chip to main processor, recording) was run in Chrome on Windows with a stand-in
  camera and a stand-in tracker. A real hand with the real tracker has not yet been
  run on every combination.
- **Expected to work.** Same browser engine as Chrome, so it should behave the same,
  but it has not been run.
- **Untested.** A different engine. It may work. If the camera or the tracker does
  not start, use Chrome or Edge. The page itself says the same when it cannot start.
- **Not available.** There is no such browser for that computer.

Use the current version of the browser. Safari on a Mac, if you try it, also needs
to be a recent version; that is expected, untested.

### What does not work, and why

| Situation | Why |
|---|---|
| Safari on Windows or Linux | Apple makes Safari only for Mac and iPhone/iPad. |
| A phone, a tablet or another computer on your network | The camera labels are accepted only from the computer running OpenMuscle, so another device cannot add or change labels. |
| Opening the Studio by the computer's network address (for example `http://192.168.1.20:8000`) | Browsers switch the camera off for pages that are not on `localhost` or https. Use `http://localhost:8000` on the computer that has the camera. |
| Very old browsers, including Internet Explorer | They cannot run the scripts or WebAssembly that the tracker needs. The page says so. |
| Using the `/webcam` page in a background tab or a minimised window | Browsers pause hidden tabs, so no labels are sent. Use the Camera panel in the dashboard instead. |
| A browser installed as a Snap or Flatpak package on Linux | These can be blocked from the camera. If the camera is not listed, try the browser from the vendor's own package. |
| A second program using the camera at the same time | Only one program can use a camera. Close Zoom, Teams or similar first. |

## Privacy

The picture never leaves the browser tab. Only joint coordinates are sent, and
only to the OpenMuscle server on this computer. No video is saved.

## Mirrored training

When the hand you want to predict cannot be measured (for example it is absent,
or it is busy holding a tool), measure the other one:

1. Put the band on the limb you want to predict.
2. Point the labeler (webcam, LASK5 or headset) at the opposite hand.
3. Move both sides together, making the same movement with each.
4. In **Start new session**, set **Mirrored** to the hand the labeler measures.

In a mirrored session one hand labels the band, so the dashboard uses whichever
camera hand is streaming best. Per-finger curl is the same number on both sides,
so nothing is transformed. Captures made in a mirrored session are tagged
`"mirror": true` in their `.meta.json` so that later tools know the labels came
from the opposite hand.

A second band on the labeled arm, at the matching position, can be recorded in
the same capture. It gives a direct comparison: one model per band, one fed by
the limb being predicted and one by the limb being measured.

## What is recorded

A webcam capture has the same layout as a headset capture, with two differences.

| | Headset (`quest_hand`) | Webcam (`webcam_hand`) |
|---|---|---|
| Label device id | `quest-left` / `quest-right` | `webcam-left` / `webcam-right` |
| `label_0..174` | 25 joints, world-space position + rotation | 25 joints, position in a hand-local frame; rotation is identity |
| `lbl_flex_*`, `lbl_ang_*` | yes | yes, same definition |
| `forearm_roll_deg`, `palm_up` | yes | no (a hand-local frame has no gravity direction) |
| `meta.json` `label_source` | `quest` | `webcam` |

Hand-local frame: origin at the wrist, y from the wrist to the middle knuckle,
x across the palm toward the index and thumb side, z = x cross y, in meters.
It describes the pose of the hand and not where the hand was in front of the
camera, so moving or turning the hand does not change the recorded pose.

The raw joint columns (`label_0..174`) are hand-local, so a left and a right hand
differ in the sign of z; they are not directly comparable across sides. The
`lbl_flex_*` columns (finger curl, 0 straight to 1 curled) mean the same on both
hands, and those are the ones to compare or pool across sides.

The camera tracker has 21 landmarks and no point for the base of each finger's
metacarpal bone. The page places that joint on the line from the wrist to the
knuckle. Knuckle flexion is measured against that line, so the reported angle
does not depend on where along the line the point sits. Expect knuckle angles
from a webcam and from a headset to differ by a few degrees for the same pose;
do not pool the two sources in one model without checking that on your own data.

The `.labels.schema.json` sidecar names the source, the tracker and version,
the frame, which joints are synthesized, which processor mode the tracker used
(`delegate`: GPU or CPU), how many frames per second it tracked (`fps`), whether
the tracker came from the local copy or the internet (`tracker_assets`), and
whether left and right were swapped.

Because the column sets differ (no forearm columns), `openmuscle train` refuses
to combine webcam captures with headset captures. Train them separately, or
pool on the shared `lbl_flex_*` columns as described in
[data-schema.md](data-schema.md).

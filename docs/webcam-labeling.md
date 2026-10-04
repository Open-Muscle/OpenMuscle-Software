# Webcam hand labeling

Use the computer's camera as the label source instead of a VR headset or a LASK5.
The camera watches a hand, a hand tracker in the browser turns the picture into
joint positions, and those are recorded next to the band data exactly like the
headset's hand tracking is.

Good for: getting started with no extra hardware, labeling the thumb and
individual joints (which the LASK5 cannot measure), and mirrored training (below).

## Quick start

1. Start the server: `openmuscle web`.
2. On the same computer, open the dashboard address with `/webcam` on the end
   (for example `https://localhost:8000/webcam`), choose the camera, and press
   **Start camera**. Allow camera access when the browser asks.
3. Hold a hand up to the camera. A skeleton is drawn over it and the five
   finger-curl bars move. Those bars are the numbers that get recorded.
4. In the dashboard, set **Labels** to **Webcam hand** and press **Record**.

The camera page needs a secure address, which `localhost` always is. On another
computer on the network it needs the HTTPS setup described in
[vr-setup.md](vr-setup.md).

The first start downloads the hand tracker (about 8 MB) from a public CDN and
the browser keeps it. To work with no internet, put a copy under
`pc/src/openmuscle/web/static/vendor/mediapipe/` and the page uses it instead:

```
vendor/mediapipe/vision_bundle.mjs        from the @mediapipe/tasks-vision package
vendor/mediapipe/wasm/                    the package's wasm folder
vendor/mediapipe/hand_landmarker.task     the hand landmarker model
```

## Getting good labels

- Palm facing the camera, all five fingers visible, about an arm's length away.
- Bright, even light. A plain background helps.
- Keep the hand in frame. When tracking is lost no labels are sent, so the
  capture simply has a gap rather than wrong values.
- Curling fingers straight toward the camera is the hardest motion for a single
  camera to measure. Turning the hand slightly so the curl is seen from the side
  improves it.
- "Hand in view" defaults to automatic left/right detection. If it guesses
  wrong, pick the hand yourself.

## Privacy

The picture never leaves the browser tab. Only joint coordinates are sent, and
only to the OpenMuscle server the page was opened from. No video is saved.

## Mirrored training

When the hand you want to predict cannot be measured (for example it is absent,
or it is busy holding a tool), measure the other one:

1. Put the band on the limb you want to predict.
2. Point the labeler (webcam, LASK5 or headset) at the opposite hand.
3. Move both sides together, making the same movement with each.
4. In **Start new session**, set **Mirrored** to the hand the labeler measures.

Per-finger curl is the same number on both sides, so nothing is transformed.
Captures made in a mirrored session are tagged `"mirror": true` in their
`.meta.json` so that later tools know the labels came from the opposite hand.

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

The camera tracker has 21 landmarks and no point for the base of each finger's
metacarpal bone. The page places that joint on the line from the wrist to the
knuckle. Knuckle flexion is measured against that line, so the reported angle
does not depend on where along the line the point sits. Expect knuckle angles
from a webcam and from a headset to differ by a few degrees for the same pose;
do not pool the two sources in one model without checking that on your own data.

The `.labels.schema.json` sidecar names the source, the tracker and version,
the frame, and which joints are synthesized.

Because the column sets differ (no forearm columns), `openmuscle train` refuses
to combine webcam captures with headset captures. Train them separately, or
pool on the shared `lbl_flex_*` columns as described in
[data-schema.md](data-schema.md).

# OpenMuscle data schema: features vs labels

Status: RATIFIED (overseer + Tory, 2026-06-30). Mirrors the canonical source of
truth AI_Team_State/DATA_SCHEMA.md (board #0302 / #0303); propose amendments to
overseer, who ratifies with Tory. Owners: vrpc + phone.

## Why this doc exists

Data-capture quality is goal #1. Inference/prediction is only a live guide, not
the deliverable. This doc is the anti-corruption guardrail so the datasets we keep
are clean and future-usable. Two rules drive everything below:

1. FEATURES (model inputs) and LABELS (model targets) never mix.
2. Never merge captures of mismatched width (this is the `combine_csvs` bug).

Small and reliable over complete.

## File format (schema-v2 CSV)

One row per matched sensor+label frame. Long format, role-tagged, separable per
hand (a two-hand capture is just interleaved rows with different role/device_id;
there is NO bilateral pivot). Column order, from `data/storage.py`:

```
ts_hub_ms, role, device_id, R{r}C{c}..., label_0..label_{N-1}, [imu_* + lbl_imu_*], [forearm_roll_deg, palm_up], [lbl_flex_* + lbl_ang_*]
```

The bracketed groups (imu, forearm, and the canonical `lbl_flex_*` / `lbl_ang_*`
block) are OPT-IN, appended after the raw `label_*`. Because they are opt-in, the
minimal schema-v2 output stays byte-identical to the golden (board #0073, phone
parity) and name-addressable readers stay backward compatible.

Lead columns (not features, not labels; provenance/keys):
- `ts_hub_ms`: hub receive clock in ms. The ONE true clock. All sync (video sync
  marker, session `started_at_ms`) uses it, never the device or headset clock.
- `role`: `left` | `right` | `labeler` (future: `gamepad`). Hub-assigned placement.
- `device_id`: the physical source.

## FEATURES (inputs, what the model learns FROM)

Everything the BAND itself produces, available at BOTH train and inference time.

1. Forearm pressure grid: `R{r}C{c}`, row-major (r outer). 15x4 = 60 cells per
   band. Raw ADC counts (~0..4095). The core signal.
2. Band IMU raw: `imu_gx, imu_gy, imu_gz, imu_ax, imu_ay, imu_az`. Raw gyro +
   accel counts from the band BMI160. Normalize with the per-chip `scale_dict`
   (advertised on announce/get_info) before training; persist that scaler so
   inference applies the identical transform (ML review #0289: train/serve parity).
3. Band IMU fused orientation: derived from `imu_*` (Madgwick/Mahony). Compute the
   SAME way at train and inference. Optional feature (toggle), not on disk today.

RATIFIED (option b, board #0303): forearm orientation is a FEATURE recomputed from
the BAND IMU fused orientation (#3 above), so it is available at inference. Proposed
column names `imu_roll_deg` / `imu_palm_up` (band-sourced, gravity-relative). The
Quest-derived `forearm_roll_deg` / `palm_up` (`forearm.py`) is kept in the `.jsonl`
sidecar as an ACCURACY CROSS-CHECK only, NOT a training input (Quest is a label-only
source, absent at deploy time; using it as a feature would be train/inference skew).
GATED: the band-IMU derivation needs the IMU mounting-axis convention (board #0200,
which band axis is up-the-arm / out-the-palm); the Madgwick fusion
(`openmuscle.fusion`) is ready.

PENDING AMENDMENT (flagged #0306): Tory also clicked "both" in the PC dashboard's
decision prompt, which would ALSO expose the Quest-derived forearm orientation as a
LABEL (a prediction target) on top of the band-IMU feature. Base = option-b; the
label add is a one-line follow-up if overseer/Tory confirm it.

Note: `lbl_imu_*` is the LABELER device's IMU (label-side context), not the band's.
Keep it out of X unless deliberately chosen.

## LABELS (targets, what the model predicts), per source

Every source maps onto ONE canonical name set (the `lbl_` prefix) so a VR session
and a LASK5 session pool into one model. Two tiers:

TIER 1, SHARED CORE (both VR and LASK5 fill it; cross-source training uses ONLY
these). Per-finger flexion NORMALIZED to [0,1] (0 = extended, 1 = curled),
wrist-relative:
```
lbl_flex_thumb, lbl_flex_index, lbl_flex_middle, lbl_flex_ring, lbl_flex_pinky   (5 cols)
```

TIER 2, EXTENDED, Quest-only (NaN-masked for non-Quest rows). Per-joint flexion in
DEGREES, wrist-relative, flexion-positive:
```
lbl_ang_thumb_mcp, lbl_ang_thumb_ip,
lbl_ang_index_mcp,  lbl_ang_index_pip,
lbl_ang_middle_mcp, lbl_ang_middle_pip,
lbl_ang_ring_mcp,   lbl_ang_ring_pip,
lbl_ang_pinky_mcp,  lbl_ang_pinky_pip                                   (10 cols)
```
Excluded from the core for reliability: DIP (noisy, PIP-coupled) and abduction/
splay (VR-derivable but LASK5 cannot label it, so it can never be shared).

Why Tier-1 is NORMALIZED [0,1] (Tory's call, #0302): LASK5 measures curl, not a
true angle, so its piston maps in with no degree-guessing; VR's measured angles
normalize into [0,1] per finger; a tendon/servo hand consumes 0..1 natively.
Tier-2 stays true degrees because only Quest fills it and it IS measured.

Per-source mapping onto the canonical names:
- VR (Quest hand-tracking): compute joint flexion angles from the 25-joint skeleton,
  wrist-relative (`openmuscle/hand_angles.py`); fill Tier-2 (per joint, degrees) and
  Tier-1 (per-finger, normalized [0,1]). Raw 175-float world-pose stays in `.jsonl`;
  NOT a training target as-is (world-frame overfit, ML #0289 finding 5).
- LASK5 (finger labeler, board #0302): 4 pistons (0..1) -> lbl_flex_index/middle/
  ring/pinky directly; the thumb joystick's flexion axis -> lbl_flex_thumb; Tier-2
  NaN-masked. Keep raw pistons + joystick X/Y + the BMI160 gyro in the `.jsonl`
  sidecar; store the per-finger piston->flexion range in meta. The joystick's 2nd
  axis captures thumb opposition/abduction (richer than Quest Tier-2 for the thumb).
  OPEN (phone to confirm): the piston finger ORDER.
- gamepad + future labelers: their own label block behind the SAME session/label
  interface. DEFERRED (do not build now, per Tory) but architected to plug in.

Current on-disk labels: `label_0..label_{N-1}` are the RAW per-source values (LASK5
pistons; Quest 175 = 25 joints x 7 [px,py,pz,rx,ry,rz,rw]). The canonical `lbl_*`
columns are appended as an OPT-IN block at capture time (see File format); the raw
`label_*` + `.jsonl` are kept for re-derivation. MIGRATION (board #0307): additive
now (raw stays, canonical appended) so the live pipeline keeps working through the
shakedown; dropping raw to `.jsonl`-only is a Phase-2 cleanup once everything
consumes `lbl_*`.

## The anti-corruption rules

1. Features and labels never mix. X = band signals only (pressure + band IMU
   [+ band-derived orientation]). y = canonical target angles. A Quest-derived
   column is a LABEL, never an input.
2. Never merge mismatched widths. `combine_csvs` MUST verify the `R{r}C{c}` count
   matches, align label columns by CANONICAL name, insert NaN for a missing DOF,
   and REJECT on a matrix-shape mismatch. (This fixes the current corruption bug
   where it writes the first header and blind-appends bodies of different width.)
3. Separable per hand. Each band's rows carry their own role/device_id; never fold
   into a bilateral pivot (Tory's separate-model-per-hand law).
4. Provenance. `session.json` snapshots the device roster + fw + roles +
   labeler_source + `started_at_ms` sync marker; captures inherit it. Debug-mode
   captures are auto-tagged and excluded from real training sets.

## Units and sign

- Tier-1 `lbl_flex_*`: NORMALIZED [0,1], 0 = extended, 1 = curled, wrist-relative.
- Tier-2 `lbl_ang_*`: DEGREES, flexion-positive, 0 = extended, wrist-relative.
- Forearm feature `imu_roll_deg` / `imu_palm_up`: degrees (gravity-relative) + bool.

## Robotic-hand transfer

Tier 1 (5 per-finger flexions) maps directly onto a 5-DOF servo/tendon hand (one
tendon per finger): the minimum viable label for goal #1. Tier 2 drives a more
articulated hand.

## Resolved / open

1. Forearm orientation: RESOLVED = option b (band-IMU FEATURE; Quest -> `.jsonl`
   cross-check). Pending amendment: Tory's "both" click (also add Quest-forearm as
   a label), flagged #0306.
2. LASK5: RESOLVED = 4 pistons + thumb joystick (#0302). OPEN: piston finger ORDER
   + the per-finger piston->flex calibration range (phone to confirm).
3. Column-name prefix: RESOLVED = `lbl_` (#0302).
4. Tier-1 representation: RESOLVED = NORMALIZED [0,1] (#0302), computed as the mean
   of each joint's angle over a nominal full-flex (MCP 90 / PIP 110 / thumb-IP 80
   deg), clamped; recalibratable per subject (range to meta).
5. OPEN: the band-IMU forearm feature is gated on the #0200 IMU mounting axes.

## Sequence (from overseer #0297)

(a) this doc drafted + ratified, (b) land wrist-relative + per-finger joint-angle
canonical labels at capture time, (c) capture the real VR + LASK5 datasets clean.
Inference stays live as a guide throughout.

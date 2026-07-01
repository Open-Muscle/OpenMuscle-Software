# OpenMuscle data schema: features vs labels

Status: DRAFT (vrpc). Owners: vrpc + phone. Ratified by: overseer with Tory.
Folds in the canonical finger-DOF proposal (board #0292 / #0295).

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
ts_hub_ms, role, device_id, R{r}C{c}..., label_0..label_{N-1}, [imu_* + lbl_imu_*], [forearm_roll_deg, palm_up]
```

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

FLAG (forearm orientation, needs a decision): `forearm_roll_deg` / `palm_up` are
CURRENTLY derived from the Quest hand (`forearm.py`), which is a LABEL source and
is NOT available at inference. So as written they cannot be a feature. Options to
ratify:
- (a) Treat `forearm_roll_deg` / `palm_up` as a LABEL (predict forearm rotation
  from muscle), OR
- (b) Recompute forearm orientation from the BAND IMU fused orientation (#3) so it
  is a true input feature available at inference.
Do not train a model that takes a Quest-derived column as an INPUT and then expect
that column to exist at inference.

Note: `lbl_imu_*` is the LABELER device's IMU (label-side context), not the band's.
Keep it out of X unless deliberately chosen.

## LABELS (targets, what the model predicts), per source

Every source maps onto ONE canonical name set so a VR session and a LASK5 session
can pool into one model. Canonical target = per-finger joint ANGLES, wrist-relative,
DEGREES, flexion-positive (0 = extended). Two tiers:

TIER 1, SHARED CORE (both VR and LASK5 fill it; cross-source training uses this):
```
flex_thumb, flex_index, flex_middle, flex_ring, flex_pinky      (5 cols)
```

TIER 2, EXTENDED, VR-only (masked/NaN for LASK5 rows):
```
ang_thumb_mcp, ang_thumb_ip,
ang_index_mcp,  ang_index_pip,
ang_middle_mcp, ang_middle_pip,
ang_ring_mcp,   ang_ring_pip,
ang_pinky_mcp,  ang_pinky_pip                                   (10 cols)
```
Excluded from the core for reliability: DIP (noisy, PIP-coupled) and abduction/
splay (VR-derivable but LASK5 cannot label it, so it can never be shared).

Per-source mapping onto the canonical names:
- VR (Quest hand-tracking): compute joint flexion angles from the 25-joint
  skeleton, wrist-relative, filling Tier 1 (aggregate per finger) + Tier 2 (per
  joint). The raw 175-float world-pose stays in the `.jsonl` sidecar; it is NOT a
  training target as-is (world-frame overfit, ML review #0289 finding 5).
- LASK5 (finger labeler): piston values (0..1 wire) map each to its finger's
  `flex_*` via a per-finger calibration range stored in meta; Tier 2 masked/NaN.
  OPEN: piston count + finger mapping + calibration range (phone to confirm).
- gamepad + future labelers: their own label block, added behind the SAME
  session/label interface. DEFERRED (do not build now, per Tory) but architected
  to plug in cleanly.

Current on-disk labels: `label_0..label_{N-1}` are the RAW per-source values today
(LASK5: 4 pistons; Quest: 175 = 25 joints x 7 [px,py,pz,rx,ry,rz,rw]). The
canonical `flex_*` / `ang_*` columns are the CAPTURE-FIX deliverable (sequence step
b): derive them at capture time, wrist-relative, and write them as the training
labels; keep `label_*` + the `.jsonl` raw for re-derivation.

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

## Units and sign (all canonical angle columns)

Degrees, flexion-positive, 0 = neutral/extended, wrist-relative frame.

## Robotic-hand transfer

Tier 1 (5 per-finger flexions) maps directly onto a 5-DOF servo/tendon hand (one
tendon per finger): the minimum viable label for goal #1. Tier 2 drives a more
articulated hand.

## Open questions (ratify with Tory)

1. `forearm_roll_deg` / `palm_up`: FEATURE (from band IMU) or LABEL (from Quest)?
2. LASK5 piston count + finger mapping + 0..1 -> degrees calibration range (phone).
3. Column-name prefix: `flex_` / `ang_` vs an `lbl_` prefix for schema-v2 parity.
4. Tier-1 aggregation: how VR per-joint angles reduce to one per-finger flex scalar
   (sum, MCP-weighted, or max).

## Sequence (from overseer #0297)

(a) this doc drafted + ratified, (b) land wrist-relative + per-finger joint-angle
canonical labels at capture time, (c) capture the real VR + LASK5 datasets clean.
Inference stays live as a guide throughout.

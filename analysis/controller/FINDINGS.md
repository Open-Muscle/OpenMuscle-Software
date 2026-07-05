# Per-hand controller decode: findings on capture_1783267460

Goal: decode Xbox controller state from forearm FMG, one model per band, each
predicting only its own hand's controls (openmuscle.gamepad_channels). Trained a
RandomForest per hand on a held-out split; ran a random split alongside as the
leakage reference.

## Headline: the pipeline is right, this capture is a poor decode target

| Band | analog R2 (random split) | analog R2 (temporal split) |
|---|---|---|
| Left  | +0.25 | -0.27 |
| Right | +0.27 | -0.08 |

- **Random split is only modest (~0.25)** and **temporal split is negative.** The
  per-hand split works and the FMG data is clean, but the model does not learn a
  decoder that generalizes across time. The random-vs-temporal gap is the
  adjacent-frame leakage the dataset paper documents, reproduced here.
- For contrast, hand-POSE decode on the same rig hits random-split R2 0.84-0.91.
  Controller decode from FMG is a much harder target, and this session makes it
  harder still.

## Why: the data, not the rig

**Band engagement was excellent** (left 1/60 flat cells, right 0/60; per-cell std
~15-17; full 0-614 range). The forearms were producing rich signal.

**The controls used were the wrong ones for FMG:**

| Control | how much it was used |
|---|---|
| Left stick  | active 12-21% of frames (thumb, weak forearm signal) |
| Right stick | active 8-12% of frames (thumb, weak forearm signal) |
| **LT / RT triggers** | **active 0.1% / 0.2% of frames** (finger flexion = strong signal, but never used) |
| A/B/Y/LB/RB/R3 buttons | pressed 0.0-0.5% of frames (no positive examples to learn) |
| X, L3 | 2.3%, 4.7% (the only buttons with any usage) |

The game was thumbstick-dominant. The controls with the STRONGEST forearm signal
(triggers, bumpers = index/middle finger flexion) were essentially never touched,
so there was nothing to learn for them. The controls that WERE used (thumbsticks)
are thumb-driven, which produces the weakest forearm FMG. Most buttons were
pressed under 1% of the time, so they are unlearnable from this take (no
positives).

## What to capture next (to actually get a forearm->controller decoder)

1. **Play something trigger- and finger-heavy.** A racing game (analog throttle
   and brake on LT/RT) exercises the strongest-signal controls continuously. Any
   game leaning on triggers/bumpers over thumbsticks will decode far better.
2. **Use the buttons, a lot and variably.** A channel pressed under 1% of frames
   can't be learned. Aim for each target control to be active a meaningful
   fraction of the session.
3. **Add a calibration warm-up.** Before playing, deliberately sweep each control
   through its full range (full trigger pulls, mash each face button, full stick
   circles) so every channel has training examples.
4. **Record more than one session** (or a long, varied one) so the train and test
   spans both cover the same control vocabulary; temporal generalization needs
   the test-time input distribution to be represented in training.

The thumbsticks will always be the weakest channel (thumb signal). Triggers,
bumpers, and grip-driven buttons are where forearm FMG should shine, once a
capture actually uses them.

Reproduce: `python analysis/controller/split_analysis.py <capture.csv>`

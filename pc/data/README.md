# Capture data root (canonical)

**`pc/data/raw/merged/` in THIS checkout is the canonical capture root.** Always
launch the server with it pinned, from anywhere:

```
openmuscle web --captures-dir "<repo>/pc/data/raw/merged"
```

Why pinning matters: the default captures dir is `data/raw/merged` **relative to
the shell's current directory**, so launching from a different folder silently
sprays captures elsewhere. That is exactly how the Jun-2026 two-hand sessions
ended up split between this root and a stray `C:\Users\<user>\data\raw\merged`
(consolidated back on 2026-07-02; the stray copies were left in place).

Backup: this checkout lives on OneDrive, so everything under this root is
cloud-synced automatically. After a keeper session, additionally zip the session
(CSV + `.sensor.jsonl` + `.label.jsonl` + `.labels.schema.json` + `.meta.json` +
the `session.json`) so it travels as one artifact.

**Models live under `data/models/` relative to the launch cwd** (same hazard):
launch the server from `<repo>/pc` (or wherever your models registry lives) so
TRAIN-BOTH models persist somewhere findable and the startup auto-restore
(newest role-tagged model per hand) sees them across restarts.

Known other copies on this box (do NOT record into these):
- `C:\Users\<user>\data\raw\merged` - stray from default-cwd launches (archived).
- `C:\dev\ttx\Open Muscle\repos\OpenMuscle-Software\...` - a second checkout;
  its data dirs are not canonical.

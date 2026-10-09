# Capture data root

Recordings are written under a "captures directory". Pin it explicitly so that
captures always land in one findable place, wherever you launch the server from:

```
openmuscle web --captures-dir "<your data folder>/raw/merged"
```

Why pinning matters: the default captures dir is `data/raw/merged` **relative to
the shell's current directory**, so launching from a different folder silently
sprays captures into a new folder each time. If you ever find recordings split
across several folders, copy them into the one you pin and keep using that one.

Backup: copy the whole captures folder to a drive or cloud-synced folder you
trust. After a keeper session, additionally zip the session (CSV +
`.sensor.jsonl` + `.label.jsonl` + `.labels.schema.json` + `.meta.json` + the
`session.json`) so it travels as one artifact.

**Models live under `data/models/` relative to the launch cwd** (same hazard):
launch the server from the same folder every time (or wherever your models
registry lives) so TRAIN-BOTH models persist somewhere findable and the startup
auto-restore (newest role-tagged model per hand) sees them across restarts.

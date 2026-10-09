# Where your data goes

Everything OpenMuscle writes lives under one folder, `~/OpenMuscle`, wherever
you start the program from:

```
~/OpenMuscle/captures   recorded takes (CSV) plus their .meta.json and label files
~/OpenMuscle/sessions   one JSON file per recording session
~/OpenMuscle/models     trained models
~/OpenMuscle/exports    exports and shareable archives
~/OpenMuscle/logs       server logs
```

To use a different folder, set `OPENMUSCLE_HOME` or start the server with
`openmuscle web --data-dir "<your folder>"`. `GET /api/storage` (or the storage
line the server prints at startup) shows the exact paths in use.

Your recordings are private. They stay on your computer, the repository's
`.gitignore` covers these file types even if you choose a folder inside a clone,
and takes are labeled with a participant code rather than a name.

Backup: copy the whole captures folder to a drive or cloud-synced folder you
trust. After a keeper session, additionally zip the session (CSV +
`.sensor.jsonl` + `.label.jsonl` + `.labels.schema.json` + `.meta.json` + the
session JSON) so it travels as one artifact.

Older sample recordings from the first bands are in `data/samples/legacy` at the
top of the repository; they are not loaded by default.

# Your data: where it is and what to send back

Everything OpenMuscle records or trains is saved on your own computer, in one folder. Nothing is uploaded anywhere. This page tells you where that folder is, what each file is, how to send recordings to someone, and how to delete or back them up.

## Where is the folder

The folder is called **OpenMuscle** and sits in your home folder:

| Computer | Default location |
|---|---|
| Windows | `C:\Users\<your name>\OpenMuscle` |
| Mac | `/Users/<your name>/OpenMuscle` |
| Linux | `/home/<your name>/OpenMuscle` |

It is in your home folder on purpose, and not in Documents, because Documents is often synced to a cloud drive and recordings are large.

Three ways to find it:

- In the Studio, scroll to **Data & Models**. The lines **Recordings are saved in** and **Trained models are saved in** show the exact folder, with **Open folder** (opens your file manager) and **Copy path** buttons.
- The window you started OpenMuscle from prints it: `Your data folder: ...`.
- On the **Captures** list, the small folder icon on a row shows that one file in your file manager. After you stop a recording, **Show file** does the same.

To keep the data somewhere else (for example a larger drive), start OpenMuscle with `--data-dir D:\OpenMuscle`, or set the environment variable `OPENMUSCLE_HOME` to that folder. See [pc-cli.md](pc-cli.md).

## What is inside

```
OpenMuscle/
    captures/    your recordings (one set of files per take)
    sessions/    one small file per session (who, which arm, which labeler)
    models/      trained models (one folder per model)
    exports/     zip files you made to send to someone, and support reports
    logs/        the installer's log (install.log); otherwise usually empty
    assets/      optional offline copy of the hand tracker (made by "openmuscle assets fetch")
    app/         the program itself (made by the installer; do not edit or copy it)
```

Only `captures`, `sessions`, `models` and `exports` hold your work. `app` and `assets` can be recreated by running the installer again.

## The files for one take

A **take** is one recording, from pressing **Record** to pressing **Stop**. Each take is a small set of files in `captures/` with the same start of name:

| File | What it is | Open it with |
|---|---|---|
| `capture_1791579889.csv` | The recording itself: one row per moment, with the band readings and the finger measurements recorded at the same time. This is the file used for training. | Excel, Numbers, any spreadsheet |
| `capture_1791579889.meta.json` | Who and what: participant code, arm, which labeler, notes you typed, the quality verdict and how long the take was. | Any text editor |
| `capture_1791579889.sensor.jsonl` | Every raw packet the band sent, kept in case a take needs to be re-processed later. Large. | Programs only |
| `capture_1791579889.label.jsonl` | Every raw packet the labeler (camera, finger labeler or headset) sent. | Programs only |
| `capture_1791579889.labels.schema.json` | Only for camera and headset takes: says which column is which joint. | Any text editor |

The part after `capture_` is a number made from the time of recording, unless you typed a **capture name** before pressing Record, in which case your name is used. After a take, **Record next take** suggests the next name in your session (for example `morning-baseline-take2`). The Studio never overwrites an existing take: if a name is taken it picks the next free one.

### What is in the CSV

The first row holds the column names. After that, each row is one moment:

- `ts_hub_ms`: the time in milliseconds on your computer's clock. Only differences between rows mean anything.
- `role`: which band made the row, `left` or `right` (the arm it was worn on).
- `device_id`: the band's own identifier.
- `R0C0` to `R3C14`: the 60 pressure readings, one column per sensing cell. They are raw numbers from the band's sensor chip (about 0 to 4095) that rise when the arm presses on the cell. They have no unit such as newtons; compare them with each other, not to a scale.
- `label_0`, `label_1`, ...: the finger measurements recorded at the same moment. These are the answers a model learns to predict. A take recorded with **No labeler** has none.
- `imu_*`, `lbl_flex_*` and similar: extra columns that appear only when the recording used them (motion sensor readings and finger bend normalised from 0 to 1).

The full description is in [data-schema.md](data-schema.md).

### The quality column

In the **Captures** list, the **Quality** column shows a word for every take:

- **Good**: nothing wrong was found.
- **Check**: it can be used, but something looks off. Hover over it for the reason, for example some finger data went missing, or the hand barely moved.
- **Bad**: do not train on it. Typical reasons: no finger data arrived, the band was not picking up pressure, or the hand tracking lost the hand for most of the take.

A small tag **too short** appears on takes under 10 seconds (aim for 30 seconds or more). Short takes are never deleted or hidden for good; they are just left out of the one-click training choice. The same verdict is saved in the take's `meta.json` and in the export's `summary.csv`.

## Participant codes

Recordings are labelled with a **participant code**, not a name. Use a short code you invent, such as `P01` or `P02`.

- Never type a real name in the **Participant code** box, a session name, a capture name, tags, notes or the video note. Those are all free text and travel with a recording when you export it.
- Keep the list that links each code to a person somewhere else (a paper sheet or a private file), not inside the OpenMuscle folder.
- Use the same code for the same person every time, so a model is not mixed up with someone else's.
- Notes and the video note are left out of an export unless you tick **Include my notes**. If you do tick it, read them first.

## Sending recordings to someone

Use the Studio's export, which packs the recordings and a plain-language explanation into one zip file.

1. In the **Session** area, click **Export session** (to send a whole session), or tick some takes in **Captures** and click **Export selected**.
2. Check the estimated size. Leave **Include full raw streams** off unless you were asked for them, and leave **Include my notes** off unless you want them shared. Click **Download zip**.
3. Your browser saves the zip, usually in your Downloads folder, and a copy is kept in `OpenMuscle/exports`. Attach it to an email or put it in your usual file-sharing tool.

From a terminal the same thing is `openmuscle bundle <session name>`; see [pc-cli.md](pc-cli.md).

The zip contains:

- `README.txt`, which explains every file and every column in plain words,
- `session.json`, the session details and participant code,
- `summary.csv`, one row per take with its length, quality and labeler,
- a `takes` folder with each take's CSV and its `.meta.json` (plus `.labels.schema.json` when there is one).

By default it leaves out the large raw streams (`.sensor.jsonl`, `.label.jsonl`), your typed notes, and your trained models. Models are never included: a model file can run code when opened, so only open one if you trust where it came from. Many email services refuse files over about 20 MB; if the zip is bigger, export one session at a time or leave the raw streams off.

### If you are asked for a problem report

Do not send recordings for a software problem. Run `openmuscle doctor --report` instead (see [troubleshooting.md](troubleshooting.md#still-stuck)). The report holds versions and check results only, and never contains Wi-Fi names, passwords or recordings.

## What to keep private

- The list linking participant codes to people.
- Raw streams and notes, unless you chose to share them.
- Trained models. They are fitted to one person's body.
- Recordings in general: treat them as personal data, because a pattern of muscle signals belongs to one person.

The camera picture is never saved. Only joint coordinates are recorded (see [webcam-labeling.md](webcam-labeling.md#privacy)).

## Deleting, moving and backing up

- **Delete one take:** in the **Captures** list click **delete** on its row and confirm. This removes the CSV and its helper files (the `.meta.json` and the two `.jsonl` files) together.
- **Delete a session:** removes only the small session description, not the takes.
- **Delete everything:** close OpenMuscle and delete the `captures`, `sessions` and `models` folders (or the whole `OpenMuscle` folder, which also removes the program; run the installer again to get it back). There is no recycle step inside the program, so empty your computer's bin too if the files must be gone.
- **Back up:** with OpenMuscle closed, copy the `captures`, `sessions` and `models` folders to an external drive or a private cloud folder. Do this after every recording day. Everything else can be recreated.
- **Restore:** copy the folders back into the `OpenMuscle` folder and start OpenMuscle.
- **Move to a new computer:** install OpenMuscle there, then copy the same three folders in.

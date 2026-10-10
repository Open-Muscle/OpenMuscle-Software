# Cold read on Windows, 2026-10-09

A cold read follows the written instructions literally, as a careful person who has
never seen the project would, and records every place where the page and the
program disagree. This one was done on Windows 11 (Windows PowerShell 5.1, Python
3.12.10 found by the installer, Chrome as the browser) with a copy of the
repository folder rather than the GitHub zip.

**How to read the log.** Each step quotes the sentence that was followed, says what
happened, and ends with a mark:

- **PASS**: the program did what the page says.
- **DIVERGES**: it did something else. Every divergence was fixed in this branch
  unless it is listed under [Still open](#still-open).
- **BLOCKED**: it could not be tried here (no band, no real hand in front of a
  camera, or it would have changed the computer outside the scratch folder).

## What was different from a real first run

These deviations are the reason some steps are marked BLOCKED, and the reason the
log does not claim more than it saw.

- Nothing touched the real profile. `OPENMUSCLE_HOME` pointed at a scratch folder,
  the Desktop and Start-menu shortcuts were redirected to scratch folders
  (`-DesktopDir`, `-StartMenuDir`), and the firewall rule was skipped
  (`-Firewall no`). The installer was started through `Install-OpenMuscle.bat` with
  those options added, from a folder whose path contains a space.
- Other copies of OpenMuscle were running on the same computer, so the Studio ran
  on port 8022 with its own UDP ports instead of the defaults. The practice kit
  was pointed at them with `--port`, `--web-port` and `--announce-port`.
- There was no kit. The practice kit (`openmuscle simulate --device-type rehearsal`)
  stood in for two bands and a labeler. One real band from another bench happened
  to be on the same Wi-Fi and showed up in the Studio by itself; it was never
  configured or written to, and it is the reason the "no arm set" notice appears
  in some steps below.
- No hand was held up to a camera. The browser was driven by a script, with a
  fake camera feed, so the camera steps stop at "camera on, no hand".
- The interface was driven by script, with screenshots and the page text read back
  after each action. Clicks that open a folder in the file manager and the
  Windows SmartScreen and firewall dialogs were not exercised.

## README.md

| # | Sentence followed | What happened | |
|---|---|---|---|
| R1 | "Getting started: the whole journey in order" and the other links under **Start here** | All 19 pages served by the in-app Help were fetched and every internal link and `#anchor` on them was checked: none broken. | PASS |
| R2 | "Start the Studio, then in a second terminal run `openmuscle simulate --device-type rehearsal`." | `openmuscle` is not on the PATH after the installer (the program lives in the `OpenMuscle/app` folder), so a person who types this line gets "not recognized". [getting-started.md](../getting-started.md#3-start-the-studio) gives the full path. | DIVERGES (see Still open: the top-level README was not edited) |

## docs/getting-started.md

| # | Sentence followed | What happened | |
|---|---|---|---|
| G1 | "On GitHub, click the green **Code** button, then **Download ZIP**. Extract the whole zip folder." | Not tried: a copy of the repository folder was used. | BLOCKED |
| G2 | "Open the `windows` folder and double-click **Install-OpenMuscle.bat**." | Ran the same file through `cmd` (with the redirections above). Six numbered steps counted by; Python 3.12.10 was found. | PASS |
| G3 | "If Windows shows a blue **Windows protected your PC** window, click **More info**, then **Run anyway**." | SmartScreen does not apply to a local run. | BLOCKED |
| G4 | "If Python 3.10 or newer is missing, the installer tries to install it for you." | Python was present. | BLOCKED |
| G5 | "Near the end it asks **Add the firewall rule now? (Y/n)**." | Skipped on purpose (it changes the system firewall). With the rule skipped the installer said what to do later: "close OpenMuscle and run this installer again". | BLOCKED |
| G6 | "Wait for **OpenMuscle is installed.**" and "a log ... in `OpenMuscle/logs/install.log`" | Both appeared, the log exists and lists every command with its exit code. The folder holds `app`, `assets`, `captures`, `exports`, `logs`, `models` and `sessions`. | PASS |
| G7 | "a shortcut called **OpenMuscle** on the Windows Desktop and in the Start menu" | Both shortcuts exist and point at the launcher inside the app folder, with the program icon and the data folder as the working folder. | PASS |
| G8 | (Troubleshooting) "run the installer again" to repair | A second run while the Studio was open stopped with "Installation stopped: OpenMuscle is open. Close the OpenMuscle window (the black one)". After closing it, the second run completed and upgraded in place. | PASS |
| G9 | "double-click **OpenMuscle** on your Desktop ... A black window opens." | Ran the launcher in the app folder. The first time it printed "OpenMuscle is already running, so opening it in your browser", because another copy was running on port 8000 (this is its documented duplicate check). With its own port it started normally. | PASS |
| G10 | The black window shows "OpenMuscle is running. Open this address in your browser ... Your data folder ..." | Shown, with the address and both folders. The "(opening it for you)" note is absent only because the launch used `--no-browser`. A "Waiting for bands" line and a firewall hint follow. | PASS |
| G11 | "The badge at the top right says **connected**. The page says **No devices found yet.**" | The badge said **connected**. The empty-state sentence on the page is **No band found yet.**, not "No devices found yet." (it could not be seen live because the stray band appeared at once). | DIVERGES (page text fixed in getting-started.md and troubleshooting.md) |
| G12 | "Want to practise with no bands? ... `openmuscle.exe simulate --device-type rehearsal`" | The command ran and two practice bands appeared, tagged left and right. But its labeler is a pretend LASK5, so **Finger data from** did not say **Webcam** and the later steps ("webcam hand data", first-model.md "choose **Webcam**") did not match. The docs never said how to get a practice labeler that matches the kit. | DIVERGES (docs now give `--labeler webcam`) |
| G13 | "In the Studio, click **Set up a device**. Follow the pages: **Get ready**, **Choose** (**A band**), **Join** ..." | **Get ready**, **Choose** and **Join** were reached and read correctly. The join page says "Type the code from the last line of the device screen", which does not say that the code is only the part after `PW`. Beyond **Join** the wizard needs a band in setup mode. | DIVERGES (join text fixed), then BLOCKED |
| G14 | "each band appears with a heatmap" | The practice bands appeared with a coloured 15-by-4 grid, a rate near `25 Hz` and `live`, and a **role** menu (**untagged**, **left**, **right**, **labeler**). | PASS |
| G15 | "In the Studio, click **Camera** to open its panel, then **Start camera**." | The panel opened, the tracker said "tracker: ready (local)" (from the offline copy the installer fetched), the picture appeared and the status read "camera on, no hand". | PASS, up to the hand |
| G16 | "You should see ... a green skeleton drawn over your hand and five finger bars" | No hand was available. | BLOCKED |
| G17 | "Check the line next to the **Record** button. It should read something like **Ready: 2 bands (left arm, right arm) + webcam hand data. Press Record.**" | With the practice webcam labeler the line read exactly that. With one untagged band also on the network it read "<band> has no arm set. Set it to Left arm or Right arm, or unplug it if you do not want to record it.", which does not help a person whose neighbour's band is on the same Wi-Fi. | PASS (message improved) |
| G18 | "Press **Record** ... Press **Stop**. ... a card that says **Saved: Good** (or **Check** or **Bad** ...)" | A 6-second take gave **Saved: Check** with "too short to train on (6 s)", and four 30-second takes gave **Saved: Good** with "This take looks good." and "Linked to first-model." Each shows **Record next take**, **Show file** and **Go to Train**. | PASS |
| G19 | "If you just stopped a take, **Go to Train** on the saved card ticks it for you." | The counter said "1 selected" but no row was ticked, so a person looking at the list would think nothing was selected. | DIVERGES (fixed) |
| G20 | "Click **Train**. It takes from a few seconds to about half a minute." | Training two bands on four 30-second takes took about 20 seconds per band. The card read **Good: explains about 98% of finger movement** with one bar per finger, "Tested on 1 recording it never saw", **What to do next** and "Prediction starts automatically." | PASS |
| G21 | "If the model scored **Fair** or **Good**, prediction starts by itself." | **Before the fix the Studio became unresponsive.** Predicting every incoming frame with the trained model could not keep up with the bands. The page stopped answering for many minutes and the black window filled with "Lost FlexGrid band" and "Found FlexGrid band" lines. After the fix it ran at about 30 percent of one core, pages answered in about 25 ms, and the green and amber hands moved together. | DIVERGES (fixed) |
| G22 | "Everything is saved on your computer, in the `OpenMuscle` folder" | True. Not mentioned anywhere: each trained model was about 500 MB on disk (and in memory while running), so a two-band training adds about a gigabyte. | DIVERGES (note added to your-data.md; see Still open) |
| G23 | "Run the check described in troubleshooting.md" (`openmuscle doctor`) | Ran with the documented command, with the Studio open: Python, packages, page files, data folder, firewall, network and self-test passed. The bands line said "No band has answered" while the Studio was receiving from one. | PASS, then DIVERGES on the bands line (fixed) |
| G24 | "Install by hand" (four lines) | Followed with the home folder swapped for a scratch folder: the private environment was made, `pip install .\pc` worked and `assets fetch` downloaded five files. About two minutes. | PASS |

## docs/first-model.md

| # | Sentence followed | What happened | |
|---|---|---|---|
| F1 | "Press **+ session** in the top strip." Fill the form: name, participant code, **Arm**, **Finger data from**, **More details**, **Planned gestures**, **Mirrored** | Every field and option named on the page exists with the same wording. The tag in the top strip showed `first-model` and its capture count. | PASS |
| F2 | "The line under the top strip should now read like this: Ready: 2 bands (left arm, right arm) + webcam hand data. Press Record." | Matched, with the practice webcam labeler. | PASS |
| F3 | "The **SENSOR** light should show a number in Hz. (The **LABEL** light is for a LASK5 only and stays at **--** with the camera.)" | **SENSOR** showed a rate. **LABEL** also showed `25Hz` while the practice camera hand was streaming. With a real camera in the browser this was not checked. | DIVERGES with the practice labeler (not changed: unverified with real hardware) |
| F4 | "Press **Record**" ... "Read the card" ... "Press **Record next take**" | Worked as written for four takes (named `01-open-close` ... `04-free-mix`). Eight takes were not recorded. | PASS |
| F5 | "While your session is running, the list shows only that session's takes." | The session box said **Active session** and showed only the session's takes. | PASS |
| F6 | "If you recorded with two bands at once ... You then get two cards, **Left hand** and **Right hand**." | Two cards, titled **LEFT HAND** and **RIGHT HAND**, each with its own verdict. | PASS |
| F7 | "Expect roughly 4 to 5 minutes for each band" | About 20 seconds per band here, with four takes. The page says these are guides, not limits. | PASS |
| F8 | "Press **Export session**" | The dialog named the session and take count and gave "about 2.0 MB". The zip had a README, `session.json`, `summary.csv` and, per take, the data, meta and label-name files. No computer name, folder, network name or address appears in any non-data file. | PASS |

## docs/studio-tour.md and the in-app Help

| # | Sentence followed | What happened | |
|---|---|---|---|
| T1 | "The top bar holds ... three things on the right" | There are four: **Set up a device**, **Help**, **stream view** and the badge. **Help** was not described. | DIVERGES (fixed) |
| T2 | Click **Help** | Opens the guides in a new tab, starting at Getting started. The list includes the reference pages. | PASS |
| T3 | "**Predict (no model yet, train first)** ... cannot be pressed" then "**Pause prediction**" | Same wording seen before and after training. | PASS |
| T4 | Opening the page for the first time | The **Finger data from** menu started on **Finger labeler (LASK5)** and a red notice said "The finger labeler (LASK5) is not sending data. Check it is turned on" to a person whose kit has no LASK5. | DIVERGES (the page now starts on **Webcam**) |

## What was changed

| Area | Change |
|---|---|
| Live prediction | Frames are predicted at most 15 times a second per band, and the loaded forest runs on one thread. This stops the freeze in G21. |
| Studio | **Go to Train** ticks the take in the list (G19). **Finger data from** starts on **Webcam** until a labeler is streaming or the person picks one (T4). The "no arm set" notice also says **Unsubscribe** (G17). The join step says the password is the part after `PW` (G13). |
| Black window | The harmless "Invalid HTTP request received." warning that the practice kit causes when it checks for https is hidden unless `--verbose` is used. |
| `openmuscle doctor` | When a Studio is already open it holds the band ports, so the check used to say "No band has answered" even with bands streaming. It now asks the open Studio which bands it is receiving from (G23). |
| Docs | The empty-state wording (G11), the practice-kit command with `--labeler webcam` (G12), the **Help** link (T1) and disk space for models (G22). |

## Still open

Each item has a one-line suggested fix.

1. **README quick start uses a bare `openmuscle`.** After the installer it is not on
   the PATH. Fix: give the full path per computer, or point at the practice-kit
   paragraph in getting-started.md. (Not edited here: the top-level README is out
   of scope for this item.)
2. **Trained models from camera hand data are about 500 MB each.** The camera hand
   gives 175 label columns, and the forest stores all of them in every leaf. Fixed:
   the trainer now learns the five finger curls the result card is scored on, caps
   the forest (at most 100 trees, minimum leaf size 3 or more, sized to stay under
   30 MB per model), and the Models list shows each model's size with a Delete
   button. The old behaviour stays available as "Learn the whole hand".
3. **Wizard pages after Join, the firewall question, SmartScreen, a real camera hand
   and a real band were not exercised.** They need a kit; the next cold read on
   real hardware should start there.
4. **The launcher's "already running" check opens whatever OpenMuscle answers on
   ports 8000 to 8010**, which on a shared computer may belong to someone else.
   Fix: have the launcher say which address it opened, so it is obvious.
5. **After the Studio is restarted, an unfinished session is moved to Past sessions
   without being ended.** Fix: say so in the banner ("Your session was closed when
   OpenMuscle stopped") or restore it as active.
6. **The model list shows the participant code in lower case** (`p01` for `P01`).
   Fix: show the code as typed.
7. **The LABEL light with the camera** (F3) should be checked on real hardware and
   the sentence in first-model.md and studio-tour.md made to match what is seen.

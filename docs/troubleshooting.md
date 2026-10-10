# Troubleshooting

Find your problem by what you see. Each entry says what is probably wrong and what to do, in order. If you are not sure where to start, run the check in the next section: it looks at your computer and network in a few seconds and names the likely cause.

- [Run the check first](#run-the-check-first)
- [Installing and starting](#installing-and-starting)
- [A band does not show up in the Studio](#a-band-does-not-show-up-in-the-studio)
- [What the band's light and screen mean](#what-the-bands-light-and-screen-mean)
- [Wi-Fi problems](#wi-fi-problems)
- [Firewall (Windows, Mac, Linux)](#firewall-windows-mac-linux)
- [The heatmap is flat or stuck](#the-heatmap-is-flat-or-stuck)
- [Two bands, or the labeler, are not recognised](#two-bands-or-the-labeler-are-not-recognised)
- [Recording problems](#recording-problems)
- [Camera problems](#camera-problems)
- [Training problems](#training-problems)
- [Prediction problems](#prediction-problems)
- [Battery and charging](#battery-and-charging)
- [Still stuck](#still-stuck)

## Run the check first

`openmuscle doctor` prints one line per check, marked `PASS`, `WARN` or `FAIL`, with the fix underneath anything that is not a pass. It ends with the most likely reason a band is not showing up.

Open a terminal and paste the line for your computer (this is the one place the kit asks you to use one). The installer put the program in the `OpenMuscle/app` folder in your home folder:

| Computer | Open a terminal | Then type |
|---|---|---|
| Windows | Start menu, type `PowerShell`, press Enter | `& "$env:USERPROFILE\OpenMuscle\app\Scripts\openmuscle.exe" doctor` |
| Mac | Press Command and Space, type `Terminal`, press Enter | `~/OpenMuscle/app/bin/openmuscle doctor` |
| Linux | Press Ctrl, Alt and T | `~/OpenMuscle/app/bin/openmuscle doctor` |

It is fine to run it while the Studio is open, and with the bands on and joined to your Wi-Fi so it can look for them. Windows may ask whether to let Python through the firewall while it runs: choose **Allow**.

**You should see** a list of lines, mostly `PASS`. If your own folder is somewhere else, replace the start of the path with it.

| If the line says | Go to |
|---|---|
| `Python version` or `Required packages` or `Web page files` FAIL | [Installing and starting](#installing-and-starting): run the installer again |
| `Data folder` FAIL | The folder cannot be written. Pick another with `--data-dir`, see [your-data.md](your-data.md) |
| `Browser page port (8000)` FAIL or WARN | [Port 8000 is busy](#port-8000-is-busy-or-another-copy-is-running) |
| `Sensor data port (UDP 3141)` or `Discovery port (UDP 3140)` FAIL | [Another copy is running](#port-8000-is-busy-or-another-copy-is-running) |
| `Network address` FAIL | Your computer is not on a network. Join your Wi-Fi |
| `Wi-Fi network` WARN, "connected to a band's own setup network" | Rejoin your normal Wi-Fi and forget the `OM-` network ([wifi-setup.md](wifi-setup.md)) |
| `Firewall` FAIL or WARN | [Firewall](#firewall-windows-mac-linux) |
| `Self-test frame` FAIL | Security software is blocking Python. Allow it, then try again |
| `Internet for the 3D hand viewer` WARN | Only the 3D hand picture needs internet; see [Camera problems](#camera-problems) |
| `Bands on the network` WARN or FAIL | [A band does not show up](#a-band-does-not-show-up-in-the-studio) |

## Installing and starting

### The command `openmuscle` is not found

You typed `openmuscle` and the computer says "not recognised" or "command not found". The program is installed in its own folder and is not on your computer's search path. Use the full path from [Run the check first](#run-the-check-first) (for example `& "$env:USERPROFILE\OpenMuscle\app\Scripts\openmuscle.exe" web` on Windows), or just start OpenMuscle from its **OpenMuscle** shortcut (on Linux, from the applications menu or by typing `openmuscle-start`), which needs no typing.

### "Python was not found" while installing

OpenMuscle needs Python 3.10 or newer, which is free. On Windows the installer tries to install it for you; if it cannot, or on a Mac or Linux:

- **Windows:** download it from [python.org/downloads](https://www.python.org/downloads/), run the installer, and tick **Add python.exe to PATH** on its first screen. Then run the installer again.
- **Mac:** download the installer from python.org, or if you use Homebrew, `brew install python`.
- **Linux:** `sudo apt install python3 python3-venv python3-pip` (Debian and Ubuntu), or your distribution's equivalent (the installer prints it).

Then run the OpenMuscle installer again. It is safe to run it again at any time; it repairs a broken install. If it stops with an error, the message says what to do, and a detailed log is in `OpenMuscle/logs/install.log`.

### The computer will not let me run the installer

- **Windows, "Windows protected your PC" (SmartScreen):** click **More info**, then **Run anyway**. The file is safe when it came from the OpenMuscle repository.
- **Mac, "cannot be opened because it is from an unidentified developer":** right-click (or Control-click) the file, choose **Open**, then **Open** again in the dialog. If the file does not run at all, open Terminal, type `bash ` (with a space), drag the file into the window, and press Enter.
- **Linux, nothing happens on double-click:** open a terminal in the folder and run `bash install.sh`.

### OpenMuscle starts but the browser shows "connection refused" or does not open

- The window that says **OpenMuscle is running.** must stay open. If you closed it, start OpenMuscle again.
- Use the address printed in that window (usually `http://localhost:8000`). If the browser did not open by itself, copy the address into Chrome or Edge.
- If there is no window at all, start OpenMuscle from the shortcut. The window prints the problem if it cannot start.
- Use **Chrome** or **Edge** for best results. Firefox and Safari are not tested.

### Port 8000 is busy, or another copy is running

Only one OpenMuscle can run at a time, because two would fight over the bands' data.

- If you see **OpenMuscle is already running at http://localhost:8000**, you already have it open. Find that window or browser tab. To stop it, close its window.
- If you see **Port 8000 is already in use ... so using port 8001 instead**, another program holds port 8000. OpenMuscle chose the next free port; use the address it printed.
- To pick a port yourself: `openmuscle web --port 8010`.
- If it says **UDP port 3141** is in use, another OpenMuscle window (or another program) has the data port. Close the other window, then start again. On Windows, `netstat -ano | findstr :3141` shows which program holds it.

### The page stops updating on Windows

Clicking inside the black OpenMuscle window can pause it (the title bar shows "Select"). Press **Enter** or **Esc** with that window selected, and avoid clicking inside it.

### A recording stopped when the laptop went to sleep

OpenMuscle asks Windows and macOS not to sleep from inactivity, but closing the laptop lid still puts it to sleep. Keep the lid open while recording. If your computer sleeps aggressively, plug it in and set Sleep to Never in Settings while you work.

## A band does not show up in the Studio

**You should see**, a few seconds after the band joined your Wi-Fi, a row for the band with a heatmap. Instead you see **No band found yet.** Check these in order:

1. **Is the band on and joined?** Its screen should show `WIFI OK` for a few seconds after joining and then the heatmap, with the light green and then pulsing (waiting for a computer) or orange (a computer is receiving). If it says `WIFI SETUP`, it is not on your Wi-Fi yet: see [wifi-setup.md](wifi-setup.md). Tap **MENU** on the band to see its status page with its address.
2. **Is your computer on the same Wi-Fi?** Check the Wi-Fi icon. A computer on the guest network, on another router, on a VPN, or still on the band's `OM-` network cannot see the band. Turn off any VPN while you work.
3. **Is the Wi-Fi 2.4 GHz and is it a normal network?** See [Wi-Fi problems](#wi-fi-problems). Guest networks and networks with a sign-in page hide devices from each other.
4. **Is the firewall blocking it?** This is the most common reason on Windows. See [Firewall](#firewall-windows-mac-linux).
5. **Wait 30 seconds, then use the search buttons.** In the Studio, click **Add a device by IP address** under the band cards, then **Scan subnet**. If you know the band's address (shown on its status page, or on your router's list of devices), type it into the box and press the plus button.
6. **Restart the band.** Turn it off and on, or unplug and replug USB-C, and wait for `WIFI OK`.
7. **Run `openmuscle doctor`** ([above](#run-the-check-first)). The last line names the most likely cause.

If a band was found before and then disappeared, the Studio's band card shows how many seconds ago it last sent data. Check the band is on, charged, and in range of the router.

## What the band's light and screen mean

The full card is [device-reference.md](device-reference.md). In short:

| Light | Meaning | What to do |
|---|---|---|
| Blue, one blink per second | Setup mode: ready for Wi-Fi details | [wifi-setup.md](wifi-setup.md) |
| Blue, two quick blinks then a pause | Setup mode, but the screen is not answering | The password cannot be read; contact support |
| Blue, steady | Joining your Wi-Fi | Wait up to 20 seconds |
| Green, steady for 2 seconds | Joined your Wi-Fi | Nothing; it is working |
| Amber, fast blink | Joining failed, going back to setup | Check the Wi-Fi name and password |
| Amber, one blink per second | Battery low | Plug in USB-C |
| Amber, slow pulse | Wi-Fi was lost, still retrying | Check the router |
| Red, fast blink | The band recovered from a crash | Usually clears within a minute; if it stays, restart it |
| Magenta, blinking | MENU and SELECT are being held | Let go now to wipe the Wi-Fi settings, or hold to 3 seconds to do it |
| Short pulse every 3 seconds (blue, red, green or white) | Waiting for a computer to connect. The colour is the band's role: blue left, red right, green labeler, white not set | Open the Studio. White means no role yet; set it in the Studio and the band's colour follows within a moment. If the Studio says the band itself was not updated, check it is on the same Wi-Fi and pick the role again |
| Orange, steady | A computer is receiving its readings | Nothing; this is the normal working state |
| Orange with a cyan flash every 5 seconds | Same, with USB plugged in | Nothing |
| Cyan, slow breathing | Paused (you tapped SELECT) | Tap SELECT to resume |
| Green, slow breathing | Recording to its memory card (Wi-Fi is off) | Tap SELECT to stop |
| Purple, pulsing | Charge mode, charging | Wait; tap SELECT to leave |
| Purple, steady | Charge mode, battery full | Unplug, or tap SELECT |

Screens: `WIFI SETUP` (set up Wi-Fi), `WIFI FAILED` (it could not join, see the reason on the next line), `WIFI OK` with an address (joined), `PAUSED`, `CHARGING`, `RECORDING`, and `LOW BATTERY`.

## Wi-Fi problems

### The band keeps going back to `WIFI SETUP`

It could not join. The top line shows `LAST TRY FAILED` and a reason: `check password` (retype it), `network not found or 5 GHz` (check the name, move closer, use a 2.4 GHz network) or `join timed out` (check the router). Run [wifi-setup.md](wifi-setup.md) again. Use **Show** in the wizard to see what you typed.

### I typed the wrong Wi-Fi and it joined something else

Hold **MENU** and **SELECT** together for 3 seconds. The light blinks magenta, and when you let go at 3 seconds the band forgets its Wi-Fi and shows `WIFI SETUP`. Then run the setup again.

### My computer has no internet after joining `OM-flexgrid-...`

That is expected while you are joined to the band's own network. Rejoin your normal Wi-Fi when the wizard tells you to, and forget the `OM-` network afterwards.

### The setup page at 192.168.4.1 will not open

Check, in order: you are joined to the band's `OM-flexgrid-...` network (not your normal one); you typed `http://192.168.4.1` with `http`, not `https`; you turned off mobile data on a phone and any VPN on a computer; and the band still shows `WIFI SETUP`. On a phone, answer yes if it asks to stay connected to a network without internet.

### The Wi-Fi is 5 GHz only

Bands cannot join 5 GHz. Most routers broadcast a 2.4 GHz network too, sometimes under the same name. Check your router's settings, or use a phone hotspot (iPhone: Personal Hotspot with **Maximize Compatibility** on; Android: choose the 2.4 GHz band).

### The network has a sign-in page, or is a guest network, or needs a username

Bands cannot sign in to a web page, and guest networks keep devices apart. This applies to many hospital, school, hotel and office networks. Use your phone as a hotspot, or a network you control. When a guest network or router setting keeps devices apart ("client isolation" or "AP isolation"), the doctor says: "the band is announcing itself, but this computer cannot connect to it".

### Is the band really on my Wi-Fi?

The band's screen shows `WIFI OK` and an address; tap **MENU** to see it again. Its light is green, then pulses or turns orange.

## Firewall (Windows, Mac, Linux)

A firewall can stop the bands' readings reaching your computer. OpenMuscle needs **incoming UDP on two ports: 3140 (bands announcing themselves) and 3141 (the readings)**. Allowing only one of them leaves the bands invisible or silent. Your computer usually asks the first time; if you chose Cancel or Block, fix it now.

### Windows

1. Easiest: in PowerShell run `& "$env:USERPROFILE\OpenMuscle\app\Scripts\openmuscle.exe" firewall`. It shows the rule it will add, asks, and only then opens the normal Windows administrator prompt. Answer **Yes**.
2. A rule applies to one network type. If Windows calls your Wi-Fi **Public**, a Private-only rule does nothing: either change the network to Private (Settings, Network and internet, Wi-Fi, your network, **Private**), or run the same command with `--public` added.
3. Or add the rule yourself, in an administrator PowerShell: `New-NetFirewallRule -DisplayName "OpenMuscle UDP 3140-3141" -Direction Inbound -Protocol UDP -LocalPort 3140,3141 -Action Allow -Profile Private,Domain`.
4. Antivirus programs with their own firewall (not Windows Security) need the same two ports allowed for Python.

### Mac

1. When macOS asks **Do you want the application to accept incoming network connections?**, click **Allow**. Allow **Local Network** access too if System Settings asks.
2. If you missed it: System Settings, Network, Firewall, Options, and allow incoming connections for Python, or turn the firewall off while you set up.
3. System Settings, Privacy and Security, **Local Network**: make sure Terminal (or Python) is switched on.

### Linux

If a firewall is on, allow the two ports: `sudo ufw allow 3140:3141/udp` (Ubuntu), or `sudo firewall-cmd --add-port=3140-3141/udp --permanent` then `sudo firewall-cmd --reload` (Fedora). Then start OpenMuscle again.

**You should see** `PASS  Firewall` in `openmuscle doctor`, and the band appearing in the Studio within about ten seconds.

## The heatmap is flat or stuck

**You should see** the grid change colour when you press on the band. If it does not:

- **Everything is black and nothing reacts to pressing.** The band is not touching the skin, or the strap is loose. Refasten it snug ([band-fit.md](band-fit.md)) and press again. If the band card says it has stopped sending (a number of seconds in red instead of `live`), see [A band does not show up](#a-band-does-not-show-up-in-the-studio).
- **It shows the colours but they never move.** The heatmap froze because the page lost its connection (the **connected** badge top right says **disconnected**). Reload the page; check the OpenMuscle window is still open.
- **The whole grid is bright with your arm relaxed.** The band is too tight, or a fold in the band presses on the skin. Loosen it and lay it flat.
- **One cell is always bright or always dark.** One sensing cell may be damaged or misaligned. If the others react, carry on, but note it in your session notes.
- **It is jumpy.** Fasten the band evenly and hold still for a moment; a loose strap that shifts gives jumpy readings.
- **Only some bands show.** See below.

## Two bands, or the labeler, are not recognised

- Each band has its own name on its card (`flexgrid-` and six characters). The Studio needs to know which arm each is on. On each band's card use the **role** menu to choose **left** or **right**. Recording is blocked with "Tell me which arm each band is on" until you do, and with "Both bands are set to the same arm" if you chose the same side twice.
- A band with a white pulse has no role yet.
- A band that is quiet for more than a couple of seconds shows its age in seconds. Recording is blocked with "A band has stopped sending data. Check it is on and charged."
- The LASK5 labeler (optional hardware) needs to be on the same Wi-Fi. If its bars stay flat, press a piston and check the **Finger data from** menu says the LASK5, not the camera.

## Recording problems

The sentence next to the **Record** button says what is wrong:

| It says | What to do |
|---|---|
| No band found yet. Turn your band on and wait about 10 seconds. | See [A band does not show up](#a-band-does-not-show-up-in-the-studio) |
| The camera is not on. Open the Camera panel on this page, press Start camera and hold your hand up. | See [Camera problems](#camera-problems) |
| The camera has stopped sending hand data. | Keep the Camera panel on this page open and the tab in front |
| Tell me which arm each band is on. | Set **left** or **right** with the **role** menu on each band's card |
| The finger labeler (LASK5) is not sending data. | Switch it on, check it is on the same Wi-Fi, or change **Finger data from** |

After you stop, the take gets a quality word (**Good**, **Check** or **Bad**) with the reason in plain words:

| Reason | Fix |
|---|---|
| Too short to train on | Record at least 10 seconds; 30 seconds or more is best |
| The band is not picking up pressure | Make sure it is snug on your arm, then record again |
| No finger data arrived / Almost no finger data came through | Check the camera or labeler is on and your hand is in view, then record again |
| Your hand barely moved | Open and close your hand through the whole take |
| Hand tracking lost your hand | Keep your hand in view of the camera |

A take that was recorded with **No labeler** is saved but cannot be used to train a model.

## Camera problems

The Camera panel says in plain words what happened. The causes and fixes (camera blocked or busy, no camera found, tracker download, left and right swapped) are in the table in [webcam-labeling.md](webcam-labeling.md#if-something-goes-wrong). In short:

- Click the camera icon in the browser's address bar and choose **Allow**; on Windows also check Settings, Privacy and security, Camera; on a Mac, System Settings, Privacy and Security, Camera.
- Close Zoom, Teams, FaceTime and other tabs that use the camera.
- The first time the camera starts it downloads the hand tracker (about 17.6 MB), so it needs internet once. After that, `openmuscle assets fetch` keeps a copy for offline use (the installer does this when you are online).
- The camera only works at `http://localhost:8000`, on the computer that runs OpenMuscle.
- **The finger bars stay flat (the labeler looks dead).** The tracker cannot see a hand: hold one hand up, palm toward the camera, all fingers visible, in good light, about an arm's length away. A flat line while your hand is clearly in view usually means the browser tab was in the background, which pauses the camera; keep the Camera panel inside the Studio page and the page in front. With the optional LASK5, flat piston bars mean the labeler is not on the same Wi-Fi or its pistons are not being pressed.

## Training problems

- **The Train button is greyed out.** Tick at least one take in the **Captures** list first.
- **"Could not train yet" with a sentence.** The sentence says what is wrong. The common ones:

  | It says | What to do |
  |---|---|
  | There are only N usable samples ... A model needs at least 150 samples (roughly 10 seconds) | Record a longer take, about a minute of slowly opening and closing each finger |
  | These recordings have no finger labels | Record again with the camera or labeler turned on |
  | The finger labeler never moved | Check it can see your hand, and move your fingers through the whole take |
  | The band was not touching the arm | Check the band sits snug against the skin and record again |
  | Every recorded row has a gap in the finger labels | Keep your hand fully in view and record again |
  | The left and right bands were never recorded at the same moment | Train one hand's takes at a time |

- **The recordings do not match.** Do not mix takes from different kinds of band (older 12-sensor and current 60-sensor) or different labelers (camera and headset), because their columns differ. Train them separately.
- **The result says Weak or Not working yet.** The model could not predict finger movement from a recording it had never seen. Record more takes with slow, clean open-and-close movements, rest between them, and keep the band in exactly the same place ([band-fit.md](band-fit.md)). Two or more separate takes give a fairer score than one.
- **A warning about different participants or sessions.** A model works best for the person and the band position it was trained on. Train one person and one session at a time.

## Prediction problems

- **Predict says "no model yet, train first".** Train a model first.
- **A banner says the model may not match.** The model was trained for another person, or for a different band than the one on that arm. Train a new model for this person and these bands, or press **Predict** to run it anyway.
- **The predicted bars do not move.** Check the band card is live and the model's result card said Fair or Good. A Weak model barely moves.
- **Predictions are wrong after taking the band off.** Put it back exactly where it was; a model learns one position ([band-fit.md](band-fit.md)).

## Battery and charging

- Plug in USB-C. The band charges whenever USB is connected. For the fastest charge, hold **MENU** for 2 seconds to enter charge mode (the screen says `CHARGING`, the Wi-Fi turns off, and the light pulses purple, then turns steady purple when full). Leaving charge mode restarts the band.
- If it does not charge, try another cable and another USB port or a phone charger.
- A band with a low battery shows an amber blink (once a second) and `LOW BATTERY` on its screen.

## Still stuck

Make a report to send to the maintainers, then open an issue at [github.com/Open-Muscle/OpenMuscle-Software/issues](https://github.com/Open-Muscle/OpenMuscle-Software/issues).

1. Run `openmuscle doctor --report` (the command from [Run the check first](#run-the-check-first), with `--report` added). It writes a file called `openmuscle-report.txt` in the folder you are in. The report contains versions and the check results only. It never contains Wi-Fi names, passwords or recordings.
2. In the issue, include:
   - the report file,
   - your computer (Windows, Mac or Linux, and the version),
   - what you did, what you expected and what happened instead,
   - what the band's screen and light show,
   - for a band that will not appear: whether your Wi-Fi is 2.4 GHz, whether it is a guest or hotel-style network, and whether the firewall prompt was answered.
3. Do not attach recordings, and do not put anyone's name in the issue. Describe the problem; you can send a recording separately if asked ([your-data.md](your-data.md)).

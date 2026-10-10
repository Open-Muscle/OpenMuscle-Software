# Getting started

You have two FlexGrid bands and a laptop. No programming is needed. This page takes you, one step at a time, from the box to a live prediction of your finger movement. Every step ends with what you should see. If you do not see it, the step links to the fix.

Allow about an hour the first time: most of it is waiting for the install and for the bands to charge.

**What you will do**

1. [Charge the bands and switch them on](#1-charge-the-bands-and-switch-them-on)
2. [Install the software](#2-install-the-software)
3. [Start the Studio](#3-start-the-studio)
4. [Put the bands on your Wi-Fi](#4-put-the-bands-on-your-wi-fi)
5. [See the heatmap and do the squeeze test](#5-see-the-heatmap-and-do-the-squeeze-test)
6. [Turn on the camera](#6-turn-on-the-camera)
7. [Make your first recording](#7-make-your-first-recording)
8. [Train your first model](#8-train-your-first-model)
9. [Watch live prediction](#9-watch-live-prediction)
10. [Where your files are](#10-where-your-files-are)
11. [If something goes wrong](#11-if-something-goes-wrong)

## 0. What you need

- Two FlexGrid bands (in your kit).
- A laptop or desktop computer with Windows, macOS or Linux, and a camera (the built-in one is fine).
- A USB-C cable and a USB power source (a laptop port or a phone charger).
- A Wi-Fi network that you know the name and password of. It must be **2.4 GHz**, must **not** be a guest network, and must **not** ask you to sign in on a web page. If yours does not suit, use your phone as a hotspot: [wifi-setup.md](wifi-setup.md#use-your-phone-as-a-hotspot) shows how.
- Internet, once, to install the software.
- The Chrome or Edge browser.

How the pieces fit together:

```
   band (left arm)  ---+
                       |   your Wi-Fi         your laptop
   band (right arm) ---+--- router/hotspot --- runs OpenMuscle Studio
                                                    |
              camera (built in) ---------------------+
              watches your hand, so the Studio knows how your fingers moved
```

The bands send their pressure readings over Wi-Fi to the laptop. The camera tells the Studio how your fingers moved at the same moment. The Studio learns the link between the two, then can predict the finger movement from the bands alone.

The finger labeler (LASK5) and a VR headset are optional extras and are not needed for this journey.

Some words on this page are explained in the [glossary](glossary.md).

## 1. Charge the bands and switch them on

1. Plug a USB-C cable into a band and into a USB power source. A band switches on whenever USB power is connected, and charges whenever USB is connected.
2. If the band does not show anything, press its power button.
3. Leave it plugged in for the first hour or two.

**You should see:** a small screen with writing, and a light on the band. A band that has never been set up shows this and blinks blue once a second:

```
WIFI SETUP
OM-flexgrid-
d7af0b
PW kd3mpq72xy
```

That is setup mode and is exactly what you want for step 4. The name and the password are different on every band. The lights and screens are listed on the [device reference](device-reference.md) card and in [troubleshooting.md](troubleshooting.md#what-the-bands-light-and-screen-mean).

Not seeing a screen at all? Try another USB cable or port and give it a few minutes. See [Battery and charging](troubleshooting.md#battery-and-charging).

## 2. Install the software

You do this once. It needs internet and takes about five to ten minutes.

**First, get the software.** On [github.com/Open-Muscle/OpenMuscle-Software](https://github.com/Open-Muscle/OpenMuscle-Software), click the green **Code** button, then **Download ZIP**. Extract the whole zip folder (on Windows right-click it and choose **Extract All**; on a Mac, double-click it). Do not run the installer from inside the zip file.

**Then run the installer for your computer.** Open the extracted folder, then the folder called `installers`:

### Windows

1. Open the `windows` folder and double-click **Install-OpenMuscle.bat**.
2. If Windows shows a blue **Windows protected your PC** window, click **More info**, then **Run anyway**.
3. The black window counts through the steps. If Python 3.10 or newer is missing, the installer tries to install it for you; if that does not work it opens the Python download page. Download it, run it, and **tick "Add python.exe to PATH"** on its first screen, then run the installer again.
4. Near the end it asks **Add the firewall rule now? (Y/n)**. Press Enter (yes). Windows then shows a normal "Do you want to allow this app to make changes" question: answer **Yes**. This lets the bands reach your computer.
5. Wait for **OpenMuscle is installed.** Press any key to close the window.

### macOS

1. Open the `macos` folder and double-click **install.command**.
2. If macOS says it cannot be opened because it is from an unidentified developer, right-click (or Control-click) the file, choose **Open**, then **Open** again.
3. If it says Python was not found, install it from [python.org/downloads](https://www.python.org/downloads/) (or `brew install python`), then run it again.
4. Wait for **OpenMuscle is installed.** in the Terminal window.

### Linux

1. Open a terminal in the extracted folder and run `bash installers/linux/install.sh`.
2. If it says Python's `venv` module is missing, it prints the command for your distribution (for Debian and Ubuntu: `sudo apt install python3-venv python3-pip`). Run that, then run the installer again.
3. If it tells you to open the firewall, copy the one line it prints (`sudo ufw allow 3140:3141/udp`) into a terminal.

**You should see:** **OpenMuscle is installed.** and a line telling you how to start it. The installer made a private copy of the program in `OpenMuscle/app` in your home folder, tried to download the camera hand tracker for offline use, ran a quick computer check, and made a launcher: a shortcut called **OpenMuscle** on the Windows Desktop and in the Start menu, an **OpenMuscle** icon on the Mac Desktop, and an **OpenMuscle** entry in the Linux applications menu. It also keeps a log of what it did in `OpenMuscle/logs/install.log`.

Problems? See [Installing and starting](troubleshooting.md#installing-and-starting). If the installer will not run on your computer, use [Install by hand](#install-by-hand) at the end of this page.

## 3. Start the Studio

1. Start OpenMuscle from its shortcut: double-click **OpenMuscle** on your Desktop (Windows and Mac), or open **OpenMuscle** from the applications menu (Linux).
2. A black window opens. **Leave it open** while you work; closing it stops OpenMuscle.
3. The first time, your computer may ask whether to let Python or OpenMuscle receive network connections (macOS: "accept incoming network connections" and "find and connect to devices on your local network"; Windows: the firewall prompt). Choose **Allow**, and on Windows keep **Private networks** ticked.

**You should see** in the black window:

```
OpenMuscle is running.

  Open this address in your browser:  http://localhost:8000   (opening it for you)
  Your data folder:                   ...\OpenMuscle
```

and your browser opens a page called **OpenMuscle Studio**. If it does not open, copy the address into Chrome or Edge. The badge at the top right says **connected**. The band area says **No band found yet.** because no band is on your Wi-Fi yet.

(If the address printed is not 8000, use the one printed. Other copies of OpenMuscle running? See [Port 8000 is busy](troubleshooting.md#port-8000-is-busy-or-another-copy-is-running).)

**Want to practise with no bands?** In a terminal, with the Studio running, start the practice kit: it pretends to be two bands and a hand seen by a camera, so you can try every step below from step 5 on (the practice hand takes the place of your own camera, so you can skip step 6). Windows: `& "$env:USERPROFILE\OpenMuscle\app\Scripts\openmuscle.exe" simulate --device-type rehearsal --labeler webcam`. Mac and Linux: `~/OpenMuscle/app/bin/openmuscle simulate --device-type rehearsal --labeler webcam`. Within a few seconds the two practice bands appear, already set to left and right, and **Finger data from** says **Webcam**. Press Ctrl+C in that terminal to stop it. Recordings made from it are practice data; delete them afterwards (the **Delete** button in the Models list frees a model's disk space). (Leave out `--labeler webcam` to practise with a pretend LASK5 finger labeler instead; then choose **Finger labeler (LASK5)** where the steps say Webcam.)

## 4. Put the bands on your Wi-Fi

You do this once per band. The Studio walks you through it.

1. In the Studio, click **Set up a device** (top right).
2. Follow the pages: **Get ready**, **Choose** (**A band**), **Join** (connect your computer to the `OM-flexgrid-...` network shown on the band's screen, using the password after `PW`), **Your Wi-Fi** (pick your network and type its password), **Finish**.
3. Your computer loses internet while it is joined to the band's own network. That is normal. The page tells you when to rejoin your usual Wi-Fi.
4. Repeat for the second band. Tick **Remember for the next device** so you do not retype the password.

**You should see:** **Band flexgrid-... is online at ...** at the end of each setup, and then on the band's screen `WIFI OK` and its address. Back in the Studio each band appears with a heatmap.

Details and every warning (2.4 GHz, guest networks, sign-in pages): [wifi-setup.md](wifi-setup.md). If a band never appears: [troubleshooting.md](troubleshooting.md#a-band-does-not-show-up-in-the-studio).

## 5. See the heatmap and do the squeeze test

1. Put a band on your forearm (see [band-fit.md](band-fit.md) for where and how tight).
2. Back in the Studio, find the row for your band: a card on the left with its name and a coloured grid on the right.
3. On each band's card, use the **role** menu to choose **left** or **right** for the arm that band is on.
4. With your arm relaxed, the grid should be mostly dark. Now press firmly on the band with a fingertip, then make a fist.

**You should see** the grid light up in pink, orange and yellow where you press, brighter as you squeeze harder, and fade when you relax. The card shows a rate such as `59 Hz` and the word `live`. The band's light is steady orange, meaning a computer is receiving its readings.

If nothing changes, see [The heatmap is flat or stuck](troubleshooting.md#the-heatmap-is-flat-or-stuck). More on the squeeze test: [band-fit.md](band-fit.md#the-squeeze-test).

## 6. Turn on the camera

The camera is how the Studio learns what your fingers are doing. (If you have no camera, skip to step 7 and choose **No labeler**; you can watch the heatmap and make recordings, but you cannot train a model.)

1. In the Studio, click **Camera** to open its panel, then **Start camera**.
2. The browser asks whether this page may use the camera. Choose **Allow**.
3. Hold one hand up to the camera, palm toward it, all fingers visible, about an arm's length away, in good light.

**You should see** your picture with a green skeleton drawn over your hand and five finger bars that move as you open and close your fingers. In the top bar the menu **Finger data from** says **Webcam**. The picture never leaves your browser and no video is saved; only the joint positions are recorded.

Problems (camera blocked, busy, left and right swapped): [Camera problems](troubleshooting.md#camera-problems) and [webcam-labeling.md](webcam-labeling.md).

## 7. Make your first recording

1. Check the line next to the **Record** button. It should read something like **Ready: 2 bands (left arm, right arm) + webcam hand data. Press Record.** If it says why it cannot record (for example which arm each band is on), fix that first; see [Recording problems](troubleshooting.md#recording-problems).
2. Rest your forearm on a table with the camera seeing your hand.
3. Press **Record**.
4. Slowly open and close your hand, five or more times. Curl one finger at a time too. Keep your hand in view of the camera. Keep going for about 30 seconds.
5. Press **Stop**.

**You should see** a timer while you record, and afterwards a card that says **Saved: Good** (or **Check** or **Bad**, with the reason in plain words) and a new row in the **Captures** list. Takes under 10 seconds are marked **too short**. Aim for 30 seconds or more; make two to four takes, resting between them.

Where it went: the **Recordings are saved in** line in **Data & Models** shows the folder, and **Open folder** opens it. See [your-data.md](your-data.md).

## 8. Train your first model

1. Scroll down to **Data & Models**.
2. In the **Captures** list, tick the takes you want to learn from (not the ones marked **Bad** or **too short**). If you just stopped a take, **Go to Train** on the saved card ticks it for you.
3. Click **Train**. It takes from a few seconds to about half a minute.

**You should see** a result card with one headline word and a plain explanation. For example **Good: explains about 82% of finger movement**. The words mean:

- **Good** (75% or more) and **Fair** (50% or more): the model follows your fingers; go and try it.
- **Weak** (25% or more): it partly works. Record more takes and train again.
- **Not working yet**: it could not predict finger movement. Check the band position and the camera and record again.

The percentage is a score measured on a recording the model never saw while learning, so it is honest. If you made only one take, the score is measured on the end of that same take and the best you can get is **Fair**. Two or more separate takes give a fairer test. The card also says **What to do next**.

For a careful first model with a movement plan, follow [first-model.md](first-model.md). Training problems: [troubleshooting.md](troubleshooting.md#training-problems).

## 9. Watch live prediction

If the model scored **Fair** or **Good**, prediction starts by itself. Otherwise press **Predict** in the top bar.

1. Keep the band on in the same place as when you recorded.
2. Move your fingers.

**You should see** the **Ground truth vs Predicted** area: next to the real finger movement (from the camera), the predicted movement drawn from the band alone. The two should rise and fall together. Now cover or turn off the camera: the prediction keeps going, from the band only.

That is the whole loop: record, train, predict.

## 10. Where your files are

Everything is saved on your computer, in the `OpenMuscle` folder in your home folder: recordings, sessions, models and exports. Nothing is uploaded. [your-data.md](your-data.md) lists every file, how to back them up, and how to export a session to send to someone.

## 11. If something goes wrong

- Run the check described in [troubleshooting.md](troubleshooting.md#run-the-check-first); it names the likely cause.
- Look up what you see in [troubleshooting.md](troubleshooting.md).
- Still stuck? [troubleshooting.md](troubleshooting.md#still-stuck) lists what to send.

## Notes for your operating system

- **Windows:** keep the black OpenMuscle window open and do not click inside it (clicking can pause it; press Enter or Esc if the page stops updating). If SmartScreen blocks the installer, use **More info**, then **Run anyway**.
- **Mac:** allow the "accept incoming network connections" prompt. The first time the camera starts, macOS asks for camera permission for your browser.
- **Linux:** if a firewall is on, run `sudo ufw allow 3140:3141/udp`.
- **Everywhere:** use Chrome or Edge. Keep the laptop plugged in and awake while recording; closing the lid puts it to sleep.
- **Camera permission:** if you chose Block by mistake, click the camera icon in the browser's address bar and choose Allow.

## Install by hand

Only needed if the installer will not run. You need Python 3.10 or newer ([python.org/downloads](https://www.python.org/downloads/); on Windows tick **Add python.exe to PATH**). Open a terminal in the extracted folder (the one that contains the `pc` folder) and type these lines one at a time. They make the same private copy of the program that the installer makes, in `OpenMuscle/app` in your home folder.

**Windows (PowerShell):**

```
py -m venv "$env:USERPROFILE\OpenMuscle\app"
& "$env:USERPROFILE\OpenMuscle\app\Scripts\python.exe" -m pip install .\pc
& "$env:USERPROFILE\OpenMuscle\app\Scripts\openmuscle.exe" assets fetch
& "$env:USERPROFILE\OpenMuscle\app\Scripts\openmuscle.exe" web
```

**Mac and Linux (Terminal):**

```
python3 -m venv ~/OpenMuscle/app
~/OpenMuscle/app/bin/python -m pip install ./pc
~/OpenMuscle/app/bin/openmuscle assets fetch
~/OpenMuscle/app/bin/openmuscle web
```

The `assets fetch` line downloads the camera hand tracker so the camera also works without internet; you can skip it. The last line starts the Studio, as in step 3. To start it again later, run just that last line, or use `python -m openmuscle web` if `openmuscle` is not found.

To update, download the new zip, extract it, and repeat the second line. To uninstall, delete the `OpenMuscle/app` folder (your recordings are in the other folders and are not touched).

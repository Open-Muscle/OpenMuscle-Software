# Glossary

Plain meanings for the words used in the OpenMuscle guides and in the Studio. Each
definition uses only everyday words or other words in this list. Words are in
alphabetical order, with the one starting with a number first.

If a word you meet is missing, please
[open an issue](https://github.com/Open-Muscle/OpenMuscle-Software/issues) so it can
be added.

## 0 to 9

**2.4 GHz** One of the two radio "lanes" a Wi-Fi network can use. The bands can only join a
network that uses the 2.4 GHz lane (the other lane is called 5 GHz). Most home
routers offer both under the same name. A phone hotspot set to "maximise
compatibility" uses 2.4 GHz.

## A

**Arm** (in a session) The side the band is worn on: **Left arm**, **Right arm**
or **Both arms**. Each band also has a [role](#r) that says the same thing.

**Assets** Extra files the Studio uses, such as the hand tracker and the 3D hand
viewer. They are normally fetched from the internet; `openmuscle assets fetch`
saves a copy on the computer so they work without internet.

## B

**Band** The OpenMuscle FlexGrid: a soft strap with a grid of pressure sensors that
you wear on the forearm. A kit has two. Also called a FlexGrid band.

## C

**Camera panel** The part of the Studio, headed **Camera**, that uses the computer's
camera to follow your hand. See [webcam-labeling.md](webcam-labeling.md).

**Capture** One saved recording. A capture is a [CSV](#c) file plus a few small
companion files. Also called a [take](#t).

**Cell** One of the 60 sensing points on a band. The Studio draws them as a grid
(4 by 15, or 15 by 4 depending on how the band reports it; the Studio prints the
size the band gives, for example `4×15`). Each cell reports how hard it is being pressed.

**Check** One of the three words for the quality of a take (Good, Check or Bad).
"Check" means usable, but worth a look. See
[studio-tour.md](studio-tour.md#quality-words-for-a-take).

**Checksum** A short fingerprint of a file. OpenMuscle checks downloaded files
against fingerprints built into it and throws away any that do not match, so a
damaged or swapped file is never used.

**Client isolation** A router setting, common on guest networks, that stops devices
on the same Wi-Fi from talking to each other. With it on, the bands cannot reach
your computer even though both are online. See [troubleshooting.md](troubleshooting.md).

**CPU and GPU** The two chips that can do the hand-tracking sums: the main
processor (CPU) and the graphics chip (GPU). The graphics chip is faster; if it
cannot do the job the camera page switches to the main processor by itself.

**CSV** A plain table saved as text, one row per line, with the values separated by
commas. Excel, Numbers and Google Sheets open it. Each recording's main file is a CSV.

## D

**dBm** The unit for Wi-Fi signal strength on a band's card. The number is
negative: closer to 0 is stronger. Below about -80 is weak.

**Debug mode** A way of starting OpenMuscle (`--debug`) that shows extra raw
numbers for troubleshooting and opens the page to your whole network. Not for
normal use.

## F

**Feature** One of the numbers a model looks at to make its guess. For a band, the
features are the 60 cell readings at one moment.

**Finger curl** How far a finger is bent, from 0 (straight) to 1 (fully curled).
This is the number the model learns to predict.

**Firewall** The part of your computer that decides which network traffic is allowed
in. It can block the bands from reaching OpenMuscle until you allow it. See
[troubleshooting.md](troubleshooting.md).

**Firmware** The program that runs inside a band or labeler (not on your computer).

**FlexGrid** See [band](#b).

## G

**Ground truth** What the labeler says your fingers are really doing. The model is
trained to match it, then judged against it. The Studio shows it next to the
prediction in **Ground truth vs Predicted**.

**Guest network** A separate Wi-Fi name that many routers offer for visitors. It
often keeps devices apart (see [client isolation](#c)) or asks for a sign-in page.
Bands do not work on one.

## H

**Heatmap** A picture of the 60 cells in colour. Dark grey is no pressure; the
colour climbs through blue, purple, pink and orange to yellow as pressure rises.

**Held-out score** The honest score for a model: how well it does on a recording it
never trained on. Shown as a percentage in the result card. See [R2 score](#r) and
[first-model.md](first-model.md#reading-the-result-card).

**Hot-swap** Loading a different model while the program is running, without
restarting it. The **Load** button on a model does this.

**Hz** "Per second". A band sending 25 Hz sends 25 readings every second.

## I

**IMU** The small motion sensor inside a band. It tells the Studio how the band is
tilted, shown in **Orientation**.

**IP address** The number that names a device on a network, such as `192.168.1.23`.
You rarely need one: the Studio finds bands by itself.

## L

**Label** One finger-position reading that the labeler provides alongside the band
readings. A model learns to predict labels from band readings.

**Labeler** Anything that measures your finger positions while you record: the
**Webcam**, a **VR headset**, a game controller, or the optional **LASK5**. A
recording with no labeler cannot be used to train a model.

**LASK5** An optional handheld labeler with four finger pistons and a thumb
joystick. It is not part of the starter kit.

**Localhost** The name a computer uses for itself. `http://localhost:8000` is the
Studio running on this computer, visible only here.

## M

**MediaPipe** The free hand-tracking tool, made by Google, that the camera page
(the hand tracker) uses to find the joints of your hand in the picture. The picture stays in your
browser.

**Mirrored training** Training for a limb that cannot be measured by wearing the
band on it and having the labeler watch the other hand while you move both sides
together. Set in a session under **Mirrored**.

**Model** The part that has learned to turn band readings into finger positions.
A model is saved as files in the models folder and can be loaded again later.

## O

**OLED** The small screen on a labeler, and on a band that has one, that shows
its name, Wi-Fi status and setup details. The kit bands do not have this screen:
read the setup name and password from the band's sticker and watch its light
instead.

## P

**Paired frame / unpaired frame** A band reading is paired when a label reading was
close enough in time (inside the [time window](#w)). Unpaired readings have no
label and are left out of the CSV.

**Participant code** A short made-up label, such as `P01`, that says whose recording
it is. Never type a real name. Older files may call this the wearer or subject.

**Piston** One of the four sliding finger sensors on a LASK5. Its position is a
label for one finger.

**Port** A numbered "door" on your computer for network traffic. OpenMuscle listens
on port 8000 for the Studio page, and on UDP ports 3140 and 3141 for the bands.

**Prediction** The model's guess at your finger positions, made live from the band
readings alone. Starting it is done with **Predict**. Also called inference.

**Pressure myography** Reading muscle activity by sensing the pressure that
working muscles put on the skin. It is how the bands work.

**PSK** The temporary password for a band's own setup Wi-Fi. It is printed on the
band's sticker (the **Setup password** line) and, on a band that has a screen,
shown on the screen after `PW`. Short for "pre-shared key".

## Q

**Quest** A Meta virtual reality headset (a VR headset) that can also track the
hands. One of the choices for a labeler.

## R

**R2 score** A number that says how well predictions match the truth. 1.0 is a
perfect match. 0 is no better than always guessing the average. Below 0 is worse
than that. The Studio shows it as a percentage and a word such as **Good**; see
[first-model.md](first-model.md#reading-the-result-card). The letter R is followed
by a small 2.

**Role** The label a band carries inside the Studio: **left**, **right** or
**labeler**. It decides which arm the band's readings are for.

**Router** The box that makes your Wi-Fi.

## S

**Sensor-only recording** A take made with **No labeler (bands only; cannot be used
to train)**. It has band readings but no finger positions, so it cannot train a
model. Use it to watch or keep band data.

**Session** A group of takes recorded in one sitting, with the participant code and
arm remembered. Start one with **New session**.

**Setup mode** What a band does when it has no Wi-Fi to join. It makes its own
temporary network (an access point), named `OM-...`, so you can tell it your Wi-Fi.
Its light blinks blue; on a band without a screen the pattern is two quick blinks
then a pause.

**Sidecar files** Small companion files saved next to a recording's CSV. They end
in `.meta.json` (who, which arm, how it went), `.sensor.jsonl` (every band reading
as it arrived), `.label.jsonl` (every label reading) and `.labels.schema.json`
(what the label columns mean). See [your-data.md](your-data.md).

**SSID** The name of a Wi-Fi network, as shown in your Wi-Fi list.

**Subnet** The group of addresses that make up your local network. **Scan subnet**
in the Studio tries each address in it, looking for bands.

**Subscribe** Telling the Studio to listen to a band. The Studio does this for you
when it finds a band; **Subscribe** and **Unsubscribe** buttons are there if a band
needs it done by hand.

## T

**Take** One recording from pressing **Record** to pressing **Stop**. The same as a
[capture](#c). Try for 30 seconds or more.

**Terminal** A window where you type a command and press Enter. On Windows it may
be called PowerShell or Command Prompt. The installers can set up a shortcut so you
do not have to use it after the first time.

**Time window** See [window ms](#w).

**Training** The step where the computer studies your takes and builds a
[model](#m). Started with **Train**. It takes a few minutes for each band, and a two-band recording trains one band after the other.

## U

**UDP** The simple way the bands send their readings over the network, in small
bursts with no reply required. A firewall must allow it in.

## V

**Verdict** The plain-language summary of a trained model on the result card: a
word (**Good**, **Fair**, **Weak**, **Not working yet** or **Not tested yet**), a
percentage, a few sentences and a next step.

**Virtual environment** A folder holding a private copy of Python (the language OpenMuscle is
written in) and OpenMuscle, so
that installing one does not disturb other software. The installers create it
for you.

## W

**Wearer** See [participant code](#p).

**Wi-Fi password** The password of your normal network. It is typed into the setup
page once; it is stored on the band so the band can rejoin, and is sent only to the
band over its own temporary network.

**Window ms** The longest time gap, in thousandths of a second, allowed between a
band reading and a label reading for them to be paired. Under **advanced**.

## Z

**Zip file** One file that holds many files packed together. **Export session**
makes one, with a plain-language README inside.

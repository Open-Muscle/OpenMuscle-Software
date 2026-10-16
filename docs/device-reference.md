# FlexGrid V4 band: device reference

What every button, light and screen on the band means, how power and charging work, and what to do when it will not connect. The first section is a one-page quick card you can print.

**Kit bands have no screen.** They ship with the small screen (SSD1306) not plugged in, and the firmware simply carries on without it. Wherever this page says "read the screen", a band without one uses the **sticker** (printed by `tools/kit_check.py`) for the setup Wi-Fi name and password, and the **light** for everything else. The screen sections are for a band that has one fitted.

> A copy of this page is kept in `OpenMuscle-Software/docs/device-reference.md`. Both copies are updated by hand; if they ever disagree, this one (next to the firmware) is the one to trust.

Every line below was checked against the firmware source and the V4 KiCad schematic (file and function in the last section). Things that can only be settled by holding a physical band are collected in [Facts still to confirm on a physical band](#facts-still-to-confirm-on-a-physical-band) instead of being guessed.

---

## Quick card (print this section)

**OpenMuscle FlexGrid V4 band**

**Switch on and charge.** The power button is the small switch at the top edge: one short press switches the band on, one short press again switches it off. Plug in USB-C to charge: the small CHRG light on the board is the charging truth.

**First time: give the band your Wi-Fi (2.4 GHz, with a password, no sign-in page).** You do this in the OpenMuscle app on your computer, over the USB cable. Your band has no screen; its light and its sticker tell you everything.

1. **Plug the band into USB first**, with a USB-C **data** cable into the computer that runs the app, and leave it plugged in until setup is finished: on battery alone the band cannot start its setup network. The light blinks blue, two quick blinks then a pause. That is normal: it means the band is waiting to be set up.
2. In the app open the device setup page (`/setup`), choose the band's USB port, type your Wi-Fi name and password and press Connect. The app sends them to the band down the cable.
3. The band restarts by itself. The light goes steady blue, then green for 2 s, then pulses while it waits for the computer. From here the band runs on battery; USB is only needed again to charge, or to set up Wi-Fi again.

**No app, or it cannot see the band?** The band's own setup network still works as a fallback: join the Wi-Fi named on the sticker (`OM-flexgrid-` plus six characters; password: the **Setup password** on the sticker, 10 characters), open `http://192.168.4.1` (type `http://`), enter your Wi-Fi name and password, press Connect, then join your normal Wi-Fi again.

(A band that does have a screen shows the same name and password on `WIFI SETUP`, and its light blinks blue once a second.)

**Buttons** (MENU and SELECT)

| Do this | What happens |
|---|---|
| Tap MENU | Status page: Wi-Fi, address, id, role (a band with a screen only). Tap again for battery and mode, again to go back. |
| Tap SELECT | Pause or resume (Wi-Fi stays on) |
| Hold MENU 2 s | Charge mode on or off (Wi-Fi off while charging) |
| Hold SELECT 2 s | Record to SD card on or off (needs an SD card) |
| Hold MENU and SELECT 3 s | Forget Wi-Fi, go back to setup (plug in USB first; keep holding until it restarts) |
| Power button (top edge) | Short press: on. Short press again: off |
| BOOT | Restart the band |

**Lights**

| Light | Meaning |
|---|---|
| Faint glow (white, can look yellow), always | Powered on. Dark means switched off (the power button latches: this glow tells on from off) |
| Only the faint glow, no blue blinks, while setting up | Setup does not start on battery alone: plug in USB |
| Blue, two quick blinks then a pause | Setup, band without a screen (the kit): join the Wi-Fi on the sticker |
| Blue, blinking once a second | Setup, band with a screen: read the screen, join the band's own Wi-Fi |
| Blue, steady | Joining your Wi-Fi (up to 20 s) |
| Green, steady for 2 s | Joined |
| Amber, fast blink | Join failed; going back to setup |
| Short pulse every 3 s | Joined and waiting for the computer. Blue = left, red = right, green = labeler, white = no role set |
| Orange, steady | Streaming to the computer |
| Orange with a cyan flash every 5 s | Streaming while USB is plugged in |
| Cyan, slow breathing | Paused (tap SELECT) |
| Purple, pulsing | Charge mode; solid purple = full |
| Green, breathing | Recording to SD, Wi-Fi off |
| Amber, one blink a second | Battery low: plug in USB |
| Amber, slow pulse | Wi-Fi lost; the band keeps retrying |
| Red, fast blink | Recovered from a crash (first minute) |
| Magenta, blinking | Both buttons held: Wi-Fi will be erased at 3 s. Let go early to cancel |

**If it will not connect:** 2.4 GHz only (the band cannot see 5 GHz); password-only networks (no sign-in page, no username-and-password networks); the computer must be on the same network and the router must let devices see each other (no guest or isolation mode); retype the password carefully; to start over, plug in USB and hold MENU and SELECT for 3 s. A laptop may jump back to a known network while you are on the setup Wi-Fi: turn off auto-join for it.

---

## Contents

- [Power, charging and the battery](#power-charging-and-the-battery)
- [Buttons](#buttons)
- [The status light (RGB LED)](#the-status-light-rgb-led)
- [The screen (OLED)](#the-screen-oled)
- [Reading the live grid](#reading-the-live-grid)
- [Modes: streaming, paused, charge, record](#modes-streaming-paused-charge-record)
- [Setting up over the USB cable](#setting-up-over-the-usb-cable)
- [Setup mode and getting back to it](#setup-mode-and-getting-back-to-it)
- [If it will not connect](#if-it-will-not-connect)
- [Facts still to confirm on a physical band](#facts-still-to-confirm-on-a-physical-band)
- [Where each fact comes from](#where-each-fact-comes-from)

---

## Power, charging and the battery

| Topic | What is true |
|---|---|
| Switching on | The power button is SW_PWR1, the switch at the top edge. It is wired to a MAX16054 soft-power controller (labelled "Power Push Button" in the schematic). A short press switches the band on. How firm a press must be is on the [confirm list](#facts-still-to-confirm-on-a-physical-band). |
| Switching off | A short press of the same button switches the band off. The firmware can also switch itself off through its power-off line (GPIO 45, "PWR_OFF"); it cannot drop the supply while USB feeds the board (it logs "Still alive, likely USB-powered" if it tries). Whether a press while USB is plugged in switches the band off is on the [confirm list](#facts-still-to-confirm-on-a-physical-band). The firmware's own on-screen power-off menu is retired; use the button. |
| Power from USB | With USB plugged in the board stays powered, even after the latch is released (the firmware's notes say so). USB also charges the battery. Whether a band that is switched off still charges is on the [confirm list](#facts-still-to-confirm-on-a-physical-band). |
| Charging hardware | A single-cell LiPo (500 mAh in the design) on a linear charger (U4, an LTC4054 or STC4054). The CHRG light is D2 ("CHRG LED" in the schematic), driven by the charger chip itself, not by the firmware. It works even if the firmware is not running. Its colour has not been checked yet (see the confirm list). It is a different light from the multi-colour status light (LED_RGB) described below. |
| What the firmware cannot see | The charger's status pin is not wired to the processor. The firmware never knows whether the charger is actually charging. It only guesses that USB is plugged in (next row). Believe the CHRG light, not the band's screen or the status light, for "is it charging". |
| How the band guesses USB | It decides USB is plugged in once the battery reads 4.05 V or more, and decides it was unplugged below 3.95 V. A very flat battery on USB therefore looks "unplugged" until it has charged past 4.05 V. A power bank behaves like USB. |
| Charge mode | Hold MENU for 2 s (or, when USB is detected, wait 5 minutes with no computer connected). Wi-Fi radio off, processor slowed to 80 MHz, so nearly all the charger's current goes into the battery. Screen (if fitted): `CHARGING / Wi-Fi off / unplug to resume / or tap SELECT`. Light: purple pulse, solid purple when full. Leaving charge mode restarts the band (it rejoins Wi-Fi in about 20 s). Unplugging USB also leaves it. |
| Charging while streaming | The battery also charges while the band streams on USB, only more slowly because the radio is on (orange light with a cyan flash every 5 s). |
| "Full" | Solid purple appears in charge mode when USB is detected and the battery has read 4.18 V or more for 5 seconds in a row. |
| Percent | A straight line from 3.50 V (0 percent) to 4.20 V (100 percent). A rough guide only; lithium cells do not discharge in a straight line. Shown on status page 2 as `BAT 87% 4.11V`. |
| Low battery | Under 3.55 V with USB not detected: amber light blinking once a second, and (on a band with a screen) a `LOW BATTERY / ... / charge soon` notice for 4 s, repeated every 5 minutes. It clears again above 3.65 V. |
| Battery life and charge time | Not measured for this document. See the confirm list. |
| Auto charge setting | `auto_charge_idle_s` (default 300). Set it to `0` to stop the band switching to charge mode by itself. |

---

## Buttons

The band has two buttons the firmware reads: **MENU** and **SELECT** (nets MENU and SELECT on the schematic, switches MENU1 and SELECT1). Three more switches are on the board and the firmware reads none of them: the **power button** (SW_PWR1, top edge), **BOOT** (BOOT1) and the switch printed **SW_RST1** (bottom left).

| Control | Action | Notes |
|---|---|---|
| Tap MENU | Show the status page | Cycle: live grid, page 1 (Wi-Fi), page 2 (device), live grid. Each page also closes itself after 15 s. |
| Hold MENU 2 s | Charge mode on or off | Happens at the 2 s mark while you are still holding. The light shows the purple preview. Leaving charge mode restarts the band. |
| Tap SELECT | Streaming to paused, or paused back to streaming | In charge or record mode a tap leaves that mode and restarts the band. |
| Hold SELECT 2 s | Record to SD on or off | Happens at the 2 s mark. Refused (nothing changes, Wi-Fi stays on) if no SD card mounts: screen shows `NO SD CARD / Record needs one / Wi-Fi stays on` for 3 s. Switched off by the setting `record_enabled: false`; the screen then shows `RECORD OFF`. |
| Hold MENU and SELECT 3 s | Forget the Wi-Fi password and return to `WIFI SETUP` | The light blinks magenta from the moment both are down. At 3 s the band erases the Wi-Fi name and password and restarts by itself, even if you are still holding. Letting go of either button before 3 s cancels it, and the button still held is ignored until both are up. |
| Power button (SW_PWR1, top edge) | Short press: switch on. Short press again: switch off | Wired to the MAX16054 soft-power controller, not to the firmware. Press feel, and whether a press switches the band off while USB is plugged in, are on the [confirm list](#facts-still-to-confirm-on-a-physical-band). |
| BOOT (BOOT1) | Restart the band | Wired to the chip's reset line (EN), not to the firmware. It does nothing else. |
| SW_RST1 (bottom left) | Nothing in normal use | Wired to the net the schematic labels `100`, which is the ESP32-S3 IO0 boot-strap pin. Hold it while you press BOOT, then let go of both: the chip starts in download mode, for reflashing a stuck board that `flash.py` cannot reach. The firmware never reads this pin (it is a boot-strap pin, so using it would change how the chip starts). |

Buttons do nothing while the band is in setup mode (it is busy waiting for your Wi-Fi details). Use them once the band has joined a network. If a band is stuck in a state you do not like, pressing BOOT or switching it off and on again always gets you back to a known place.

---

## The status light (RGB LED)

One table covers every state. The right-hand column is the name of the state in the firmware (`lib/status_led.py`, palette `PALETTE_DEFAULT`).

How to read it: the colour says what kind of thing is happening, the pattern says which. Orange and amber are the same colour; steady means streaming and anything that blinks or pulses means trouble. When two things are true at once the one nearest the top of the second table wins.

A powered band is never fully dark. A faint white glow (it can read yellow on the LED) sits under every pattern (the dark half of a blink, the gaps of the 3 s pulse), from the first moment the firmware runs. So dark means one thing only: the band is switched off. The power button latches, so this glow is also how you tell a press that switched the band on from one that switched it off.

If the light stays at the faint glow and never blinks blue (two quick blinks, or one a second) on a band that has no Wi-Fi saved, the band is on battery alone, and on battery alone the band cannot start its setup network. Plug in USB; if the blue blink does not start, press BOOT to restart the band. A band that already has its Wi-Fi runs on battery without USB.

### All states

| Light | Meaning | State name |
|---|---|---|
| Dim blue, breathing, for a moment | Switching on | `boot` |
| Blue, one blink per second | **Setup mode**, band with a screen. Read the screen and join the band's own Wi-Fi. | `setup` |
| Blue, two quick blinks then a pause | **Setup mode, no screen fitted.** This is the normal setup pattern for a kit band, not a fault: read the Wi-Fi name and password from the sticker. | `setup_no_oled` |
| Blue, steady | **Joining** your Wi-Fi | `joining` |
| Green, steady for 2 s | **Joined** (also each time Wi-Fi comes back) | `joined` |
| Amber, fast blink | First join **failed**; the band is going back to setup | `join_failed` |
| Red, fast blink | Recovered from a crash (watchdog restart). Clears itself after 60 s of normal running. | `fault` |
| Amber, slow pulse (never fully dark) | **Wi-Fi lost.** The band keeps retrying and reconnects when the network returns. | `wifi_lost` |
| Amber, one blink per second | **Low battery** (under 3.55 V, USB not detected) | `low_battery` |
| Magenta, blinking | MENU and SELECT are both held. At 3 s the Wi-Fi settings are erased. | `ap_provisioning` |
| Blue, brief pulse every 3 s | Joined, waiting for the computer. Role: **left** | `idle_left` |
| Red, brief pulse every 3 s | Joined, waiting for the computer. Role: **right** | `idle_right` |
| Green, brief pulse every 3 s | Joined, waiting for the computer. Role: **labeler** | `idle_labeler` |
| White, brief pulse every 3 s | Joined, waiting for the computer. **No role set** | `idle_unassigned` |
| Orange, steady | **Streaming** to a computer | `streaming` |
| Orange with a short cyan flash every 5 s | Streaming while USB is plugged in | `streaming_on_usb` |
| Cyan, slow breathing (3 s) | **Paused** (tap SELECT to resume) | `standby` |
| Green, breathing (1 s) | **Recording** to SD, Wi-Fi off | `record` |
| Purple, slow pulse (1.5 s, never fully dark) | **Charge mode** | `charging` |
| Purple, steady | Charge mode and the battery is full | `charge_full` |

The start-up story in order: blue blink (setup), blue steady (joining), green for 2 s (joined), then the pulse in the role colour, which becomes steady orange when a computer starts receiving.

### Which one shows when several are true

Highest first, as resolved by `_resolve_led_state` in `flexgrid.py`:

1. Red fast blink (crash recovery)
2. The button preview: magenta (both buttons held), purple (MENU held 2 s), green (SELECT held 2 s)
3. Amber fast blink (join failed)
4. Green for 2 s (just joined)
5. Blue steady (joining)
6. Green breathing (recording)
7. Cyan breathing (paused)
8. Purple (charge mode)
9. Amber blink (low battery)
10. Amber slow pulse (Wi-Fi lost)
11. Orange (streaming), with the cyan flash on USB
12. The role-colour pulse (waiting)

Setup mode (blue blink) is its own program that runs instead of all of this.

If the light is completely dark, the band is switched off. Press the power button once and the faint glow appears at once.

This is the status light (LED_RGB on the schematic). The separate CHRG light (D2) next to the charger is not part of this table.

---

## The screen (OLED)

**Kit bands do not have a screen.** The board has a header for it and the kit leaves it unplugged. The firmware notices at start-up (it logs `No screen` and carries on), so nothing is broken: the setup name and password are on the sticker, and the light, the app and this page cover everything else. Everything in this section applies only to a band that has a screen fitted.

The screen is 128x32 pixels: four lines of 16 characters. Every screen below is written for four lines. (A 128x64 panel also works with `"display_height": 64` in settings, and shows eight lines.)

### Screens in the order you meet them

| Screen | When | Exact lines |
|---|---|---|
| Setup | No Wi-Fi saved (new band, or after a reset) | Line 1 rotates every 3 s: `WIFI SETUP`, `see guide`, and after a failed attempt `LAST TRY FAILED` then the reason. Lines 2 and 3: the network name split in two, for example `OM-flexgrid-` and `d7af0b` (read as one name). Line 4: `PW ` and the 10-character password. |
| Boot | Switching on, while joining | `OpenMuscle V4` / the band's id (`flexgrid-d7af0b`) / `Joining Wi-Fi...` / your network name |
| Join failed (about 2 s) | First join did not work | `WIFI FAILED` / the reason (one or two lines) / `back to setup` |
| Status page 1 | MENU tap; also the first 15 s after the band joins | `WIFI OK` / the band's **IP address** / the id / `role: left` (or `no role set`) |
| Status page 2 | Second MENU tap | `BAT 87% 4.11V` (or `LOW 12% 3.42V`) / `MODE STREAM` / `HUBS 1` / `NET ` and your network name |
| Live grid | Normal running | The 15 x 4 pressure heatmap (see below) |
| Paused | Tap SELECT | `PAUSED` / `Wi-Fi is on` / `not streaming` / `SELECT to resume` |
| Charging | Hold MENU 2 s, or USB plugged in and idle | `CHARGING` / `Wi-Fi off` / `unplug to resume` / `or tap SELECT` |
| Recording | Hold SELECT 2 s (SD card needed) | `RECORDING` / `to SD, Wi-Fi OFF` / `tap SELECT to` / `stop & reconnect` |

Variants of status page 1: `JOINING WI-FI` and your network name while it joins; `WIFI LOST / retrying...` when the network has gone; `WIFI OFF / CHARGE mode` (or `RECORD mode`) when the radio is switched off on purpose. On page 2, `MODE` reads `STREAM`, `PAUSED`, `CHARGE` or `RECORD`, `HUBS` is the number of computers receiving from the band (up to 4), and a network name longer than 12 characters is cut off at the screen edge.

### Short notices (3 to 4 seconds, then back to what you were looking at)

| Text | Why |
|---|---|
| `NO SD CARD / Record needs one / Wi-Fi stays on` | You held SELECT but no card mounted; nothing was changed |
| `RECORD OFF / disabled in / settings` | Recording is switched off in this band's settings |
| `LOW BATTERY / BAT 12% 3.42V / charge soon` | Battery under 3.55 V; repeats every 5 minutes |

Banners (Paused, Charging, Recording) stay up until you leave the mode. A notice or a status page temporarily covers a banner.

### Why a setup name is on two lines

The network name is 18 characters (`OM-flexgrid-` plus six letters or digits) and the screen holds 16, so it is split. The password never moves, so you can copy it from the screen without waiting for the top line to change. The password uses only the letters and digits `abcdefghijkmnpqrstuvwxyz23456789`: there is no zero, no capital letters, no letter o, no letter l and no number 1, so what you see cannot be mistaken for something else.

---

## Reading the live grid

The 60 sensors are drawn as a 15 by 4 grid of 8-pixel cells that fills the screen. Harder pressure draws a bigger mark in that cell:

| Pressure on the sensor | Mark in the cell |
|---|---|
| Almost none | blank |
| Light | a single dot |
| Medium-light | a small square |
| Medium | a larger square |
| Hard | the whole cell filled |

The band scans the sensors about 50 times a second and streams every scan to the computer. The screen redraws about 5 times a second, so it looks calmer than the data really is. The screen is a convenience check ("is the sensor alive?"); the app shows the real data.

---

## Modes: streaming, paused, charge, record

| Mode | Wi-Fi | Sends data | How you get there | How you leave |
|---|---|---|---|---|
| Streaming (normal) | On | Yes, to every computer that has subscribed | Default after switching on | Tap SELECT (to paused) |
| Paused | On | No (the computer stays connected) | Tap SELECT | Tap SELECT |
| Charge | Off | No | Hold MENU 2 s, or 5 minutes idle with USB detected | Tap SELECT, hold MENU 2 s, or unplug USB. The band restarts. |
| Record | Off | No; writes to the SD card | Hold SELECT 2 s (SD card required) | Tap SELECT, or hold SELECT 2 s. The band closes the file, restarts and rejoins Wi-Fi. |

Record mode writes one file per session to `sessions/` on the SD card, a CSV with a time stamp, the 60 sensor values, four label columns (empty when nothing is labelling) and six motion-sensor columns. It is an optional offline mode; the normal way to record is to stream to the app.

---

## Setting up over the USB cable

This is the normal way to give a band its Wi-Fi. The app sends the Wi-Fi name and password to the band down the USB cable, so you never have to join the band's own network or open a web address.

What you do:

1. Plug the band into the computer that runs the app with a USB-C **data** cable (some cables only charge; if the app cannot see the band, try another cable). Leave it plugged in.
2. Start the app (`openmuscle web`) and open the device setup page (`/setup`).
3. If the app asks which USB port the band is on, choose it.
4. Type your Wi-Fi name and password (2.4 GHz, with a password, no sign-in page) and press Connect.
5. The band saves them and restarts by itself, which takes a few seconds. Its USB port disappears and comes back; the app handles that. Then the light goes steady blue (joining), green for 2 s (joined), and pulses while it waits for the app.

If the join fails (a typo in the password, or a 5 GHz-only network) the band forgets the details, blinks amber and comes back to the blue two-blink setup pattern; send them again from step 4. The same cable channel can also erase a band's Wi-Fi and set its left or right role.

The setup network and the `192.168.4.1` page described below stay in the band as the fallback. Use them when you have no app, or the app cannot open the band's port.

Developers: the exact commands and replies are in [usb-console.md](usb-console.md).

---

## Setup mode and getting back to it

A band with no Wi-Fi saved starts **setup mode**. It becomes its own Wi-Fi network named `OM-flexgrid-<6 characters>` (WPA2, channel 1, up to 4 devices) and serves a small page at `http://192.168.4.1`. This is the fallback route: the normal one is [over the USB cable](#setting-up-over-the-usb-cable), which works in setup mode too.

**Do the Wi-Fi setup with the USB cable plugged in.** On battery alone the band cannot start its setup network: the light stays at the faint glow and never blinks blue. Once the band has its Wi-Fi, it joins and runs on battery with no cable. The same goes for starting over: hold MENU and SELECT for 3 s with USB plugged in, so the band can come back up in setup mode.

**A band without a screen (the kit).** The band cannot show its name and password, so they come from the **sticker**: `Setup Wi-Fi` is the network name and `Setup password` is the 10 characters. The light blinks blue, two quick blinks then a pause; that is the normal setup pattern. Wherever the steps below say "from the screen", read "from the sticker". The sticker is the only place the password is written down. If it is lost, hold MENU and SELECT for 3 s to take the band back to setup (the setup name and password are kept), then run `tools/kit_check.py <port>`: it prints them even when the band is not in factory state or is still charging (marked read only, not sticker text). Or reflash the band for a new password and sticker.

1. With USB plugged in, join that network from a phone or laptop with the password from the screen (or the sticker).
2. Open `http://192.168.4.1` in a browser. Use `http://`, not `https://`. Some phones open the page by themselves; if yours does not, type the address.
3. Type your Wi-Fi name and password and press Connect. The page says it saved and that the band is restarting.
4. Join your normal Wi-Fi again on the phone or laptop.
5. The band restarts and tries to join. A steady blue light shows for up to 20 s (a band with a screen also shows `Joining Wi-Fi...`). On success the light goes green for 2 s and a band with a screen shows `WIFI OK` and the address for 15 s; then the live grid. Without a screen, the light going green and then pulsing in the role colour is your sign that it joined.

The PC app can also do this for you over the USB cable (see above) from its device setup page (`/setup` in the app); see the app's Wi-Fi guide.

**If the first join fails** the band does not retry for ever. It shows `WIFI FAILED` and the reason for about 2 s, blinks amber, forgets the password, restarts and is back in setup mode with the **same network name and password as before** (about 30 s in all). The top line of the setup screen then also shows `LAST TRY FAILED` and the reason. Without a screen, the sign is the fast amber blink followed by the blue two-blink pattern again. Join its network (name and password from the sticker) and enter the details again.

| Reason on screen | What it usually means |
|---|---|
| `check password` | The password was typed wrong |
| `network not found or 5 GHz` | Name misspelled, out of range, or the network is 5 GHz only |
| `join timed out` | The router did not answer in 20 s |

**Once a band has joined successfully it never forgets the password by itself.** If the router is off for a day, the band waits and reconnects when it returns (amber slow pulse meanwhile).

**To start over** (new Wi-Fi, new place): on a joined band hold MENU and SELECT for 3 s. The setup network name and password stay exactly as printed on the band's sticker. A band that is still in setup mode needs no reset.

A reflash or `wipe.py` (for people preparing kits) gives the band a **new id and a new setup password**, so an old sticker is then wrong.

---

## If it will not connect

| Check | Why |
|---|---|
| The network is 2.4 GHz | The band's radio cannot see 5 GHz. Most routers broadcast both. If yours merges them under one name and the band still fails, switch on a separate 2.4 GHz name. A phone hotspot must be set to 2.4 GHz (on an iPhone choose "Maximize Compatibility"). |
| It is a normal password network | No sign-in page (hotel, school, cafe, some offices), no username-and-password (enterprise) network. WPA2 (or a mixed WPA2 and WPA3 mode) is what the band is built for; a WPA3-only network has not been tried. |
| The password is exactly right | Case counts. 8 to 63 characters. Look for a swapped 0 and O. If `check password` appears, type it again slowly. |
| The computer is on the same network | The app finds the band by listening for its announcements (UDP 3140). If the computer is on a different network, a guest network, or a router with "client isolation" or "AP isolation" on, the two cannot see each other. |
| The computer's firewall lets it in | On Windows the first run may ask permission: allow private networks. The app has a `openmuscle firewall` command and a `doctor` command to check. |
| While on the setup Wi-Fi, the laptop keeps leaving it | A computer with "no internet" on the setup network may jump back to a known network. Turn off auto-join for the setup network, join it, finish setup, then turn it back on. A phone may ask "stay connected?": say yes. |
| No setup network, and the light only glows faintly | The band is on battery alone, which cannot start the setup network. Plug in USB; if the blue blink does not start, press BOOT. |
| Nothing at `192.168.4.1` | Make sure you are joined to the band's network, not your home one. Use `http://`. Turn off a VPN. Try the address from a phone. |
| Still stuck | Hold MENU and SELECT for 3 s (joined band) to return to `WIFI SETUP`, or switch the band off and on. |

---

## Facts still to confirm on a physical band

These are not guessed in this document. Each one needs someone holding a band. When settled, move the answer into the right section above and delete the line here.

Already settled from the V4 schematic and board, so not listed: which switch is the power button (SW_PWR1, short press on and short press off), that MENU1 and SELECT1 are the firmware buttons, that BOOT1 is the chip reset, that SW_RST1 is the IO0 boot-strap switch, and that kit bands ship without the screen.

1. **Power button feel.** How firm a press must be, whether the band ever needs a longer hold, whether a press while USB is plugged in switches the band off, and whether a band that is switched off still charges when USB is plugged in.
2. **CHRG light.** The colour of D2, whether it is visible through the case or sleeve, whether it lights while charging and goes out when full, and how it behaves with no battery connected.
3. **Charging.** Time from empty to full on a computer port and on a wall charger, and the actual running time while streaming.
4. **Charge-state accuracy.** Battery percent against a meter; whether the 4.05 V USB guess behaves with the supplied cable and a typical charger.
5. **SD socket.** Whether a card seats and is read, and whether the kit ships with or without one.
6. **Light visibility.** Whether the status light is readable through the case or sleeve in daylight, and the magenta, purple and green previews are easy to tell apart.
7. **Tap and hold feel.** The 2 s and 3 s holds are comfortable with the case or glove on.

---

## Where each fact comes from

| Topic | Source |
|---|---|
| Every LED colour and pattern | `lib/status_led.py` (`PALETTE_DEFAULT`, `animate`) |
| Which LED state wins | `flexgrid.py` `_resolve_led_state` |
| Button actions and timings | `flexgrid.py` `power_mode_loop` (tap, 2 s and 3 s holds) |
| BOOT is the reset line, SW_RST1 is IO0, the power button | `lib/pinmap.py` (button notes) and the V4 KiCad schematic (`OM-FlexGrid-Rigid-PCB.kicad_sch`: SW_PWR1, BOOT1, SW_RST1 on net `100` = IO0, MENU1, SELECT1, D2) |
| No screen is normal | `lib/display_manager.py` (logs `No screen`), `flexgrid.py` `main` (setup LED choice), `tools/kit_check.py` (screen is a warning) |
| Every screen's words | `lib/screens.py`; heatmap in `lib/display_manager.py` |
| Which screen shows when | `flexgrid.py` `_screen_override`, `_status_info` |
| Battery curve, USB guess, full | `lib/power_manager.py` |
| Low battery limits | `flexgrid.py` `update_low_battery` |
| Setup mode, password rules, page | `lib/provisioning.py`, `flexgrid.py` `main` |
| First join fails and goes back to setup | `flexgrid.py` `_fall_back_to_setup`, `lib/network_manager.py` |
| Defaults (auto charge, record, height) | `lib/settings_manager.py` |
| Record file format | `lib/sd_logger.py` |
| Power button parts | KiCad V4 schematic and parts list (OpenMuscle-FlexGrid repo, `BOM-Human-OM-FlexGrid-Rigid-PCB.csv`) |

The desktop tests (`tests/test_docs.py`) check that every light, screen line and button named here still exists in those files.

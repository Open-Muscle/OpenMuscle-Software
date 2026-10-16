# Put your band on Wi-Fi

A new band does not know your Wi-Fi yet. This page walks you through telling it, once, so it can join your network and send its readings to your computer. It takes about two minutes per band with the USB cable (Method 1), or about five minutes through the band's own Wi-Fi network (Method 2). You do not need to type any commands.

If you are still at the start of the kit journey, go back to [getting-started.md](getting-started.md). If a step here does not behave as described, see [troubleshooting.md](troubleshooting.md).

## What is happening

You give the band the name and password of your own Wi-Fi, once. The band keeps them in its own memory, restarts, joins your Wi-Fi, and shows up in the Studio within about 20 seconds. There are two ways to hand them over:

- **Method 1: the USB cable (recommended).** You plug the band into your computer with its USB cable and the Studio sends the Wi-Fi details down the cable. Your computer stays on your usual Wi-Fi the whole time, so nothing else on it stops working.
- **Method 2: the band's own Wi-Fi.** Until it knows your Wi-Fi, a band can make a small Wi-Fi network of its own, named like `OM-flexgrid-d7af0b`. You join that network from your computer for a minute and type your Wi-Fi details into a page. Use this if you have no cable that carries data, or for the optional finger labeler.

Your Wi-Fi password is stored in the band's own memory, so you only do this once per band (and again only if you change networks).

## Before you start

Check these first. Almost every setup problem comes from one of them.

- **The Wi-Fi must be 2.4 GHz.** The band cannot see 5 GHz networks. Most home routers broadcast both a 2.4 GHz and a 5 GHz network, often under one shared name, and the band will use the 2.4 GHz one. If your router lets you split them, the 2.4 GHz one is sometimes labelled "2.4G" or "-2G".
- **It must have an ordinary password** (WPA2). Open networks with no password also work, but a password is better.
- **It must not ask you to sign in on a web page.** Hotel, school, hospital, airport and many office networks show a sign-in page ("captive portal") the first time you connect. A band cannot click through that page, so it never gets online.
- **It must not be a guest network.** Guest networks usually keep devices apart on purpose ("client isolation"), so your computer cannot see the band even when the band is connected. Use your main network.
- **It must not need a username.** Work and school networks that ask for a username and a password (WPA2-Enterprise) will not work.
- **Your computer and the bands must end up on the same network.** The computer uses the same Wi-Fi (or a cable into the same router).

If your network fails any of these, use your phone as a hotspot for the whole session (next section). It is the most reliable fix and costs nothing.

### Use your phone as a hotspot

- **iPhone:** Settings, then Personal Hotspot, turn on **Allow Others to Join**, and also turn on **Maximize Compatibility** (that makes the hotspot use 2.4 GHz).
- **Android:** Settings, then Hotspot (the exact name varies), and choose the **2.4 GHz** band.

Keep the hotspot switched on until every band is set up, and join your computer to the same hotspot afterwards. You do not need mobile data for this; the bands and the computer only need to talk to each other.

## Method 1: the USB cable (recommended)

You need the Studio running first (see [getting-started.md](getting-started.md), step 3), and the band's USB-C cable. The cable must carry data: a few cables only charge, and a band on one of those is never found. The cable that came with the kit works. The wizard works only in the browser on the computer that runs OpenMuscle.

1. **Plug the band into your computer.** Use the USB cable and a USB port on the computer itself (not a wall charger, and a hub can get in the way). Look at the light. A band that is powered always shows a **faint glow** (it can look white or yellow); a dark light means the band is switched off. If the light is dark, press the power button at the top edge once: the faint glow tells you that the press switched the band on. Keep the cable plugged in until setup is finished.

2. **Open the wizard.** In the Studio, click **Set up a device** in the top right. (Or open `http://localhost:8000/setup`; use the address your start-up window printed if it is not 8000.) Read the short checklist (it repeats the section above), click **I am ready**, then click **A band**.

3. **Wait for the band to be found.** The page is titled **Plug the band into this computer**. Within a few seconds the line **Looking for a band on the USB cable...** turns into a green tick and **Found Band flexgrid-d7af0b (left arm)** (your band's name will differ). It asks **Does the Setup Wi-Fi line on its sticker end with d7af0b? If not, this is a different band.** Check the last six characters against the sticker.

   If you plug in two bands, the page says **More than one band is plugged in. Choose the one to set up now, or unplug the others.** Tick the one you want. A band that already has a Wi-Fi saved says so (**This band is already set up for "your Wi-Fi name". Sending new details replaces them.**).

4. **Choose your Wi-Fi.** The **Wi-Fi name** box is already filled in with the Wi-Fi your computer is on right now. Check that it is the one the band should join (and 2.4 GHz), or type another exactly, including capital letters and spaces. Type the **Wi-Fi password**; **Show** lets you check it. Leave **Remember for the next device (this browser tab only)** ticked if you have a second band to set up: the details are kept only in this browser tab and never saved on disk. Click **Send to the device**.

5. **Wait about 20 seconds.** The page says **Saved on the band. It is restarting (about 20 seconds) and will join "your Wi-Fi name". Keep it plugged in until it is online.** and shows a checklist that ticks off as the band restarts and appears. Your computer does not leave its Wi-Fi, so there is nothing to reconnect.

6. **Done.** You should see **Band flexgrid-d7af0b is online at 192.168.x.x** (the numbers are the band's address on your network; you never need to type them). The band's light is green for two seconds, then steady orange once your computer is receiving its readings. From now on the band also runs on battery alone, so you can unplug the cable. Click **Set up the next device** for the second band, or **Go to the live view**. For the second band the page offers **Send the same Wi-Fi details**, so you do not type the password again.

Nothing to tidy up afterwards: your computer never joined the band's network.

### The same thing with one command

If you prefer a terminal, plug the band in as in step 1 and run (the first line is for Windows, the second for Mac and Linux; use the program path from [troubleshooting.md](troubleshooting.md#run-the-check-first)):

```
& "$env:USERPROFILE\OpenMuscle\app\Scripts\openmuscle.exe" setup-usb --ssid "Your Wi-Fi name"
~/OpenMuscle/app/bin/openmuscle setup-usb --ssid "Your Wi-Fi name"
```

It asks for the password and shows nothing while you type. It finds the one band that is plugged in, tells you its name, and says that the band is restarting to join your Wi-Fi. If more than one band is plugged in, add `--port` with the port name it lists (for example `--port COM5`), or unplug the others. `setup-usb --forget` makes a band forget its Wi-Fi. Use either the wizard or the command for a band, not both at the same time.

### If the band is not found, or does not answer

- **No band found, the page keeps looking.** Check the cable is pushed in at both ends. Try another cable (a charge-only cable carries power but no data) and another USB port on the computer, without a hub. The kit cable is a data cable.
- **A band is plugged in but is not answering.** A band that has been plugged in for a while with nothing listening can stop answering. Unplug the cable and plug it back in (or press the BOOT button), then try again.
- **More than one band is found.** Unplug all but the one you are setting up, or choose it in the list.
- **The page says Could not open the USB port.** Another program may be using the band, for example a serial monitor or a flashing tool. Close it, then unplug and replug the band.
- **The band says it does not know how to be set up over USB.** Its software is older than this page. Use Method 2, or ask for a firmware update.
- **It was sent but never shows up in the Studio.** The band tried your Wi-Fi and could not join. Go through [If the band does not appear within a minute](#if-the-band-does-not-appear-within-a-minute) below.

More about USB problems: [troubleshooting.md](troubleshooting.md#setting-up-over-the-usb-cable).

## What you will notice with Method 2

While your computer is joined to the band's `OM-` network it **has no internet**. Web pages stop loading and your computer may say "no internet" or "connected, no internet". That is normal and lasts only a few minutes. The setup page keeps working because it runs on your own computer. Method 1 does not have this effect.

## Method 2: the band's own Wi-Fi

Use this when you have no cable that carries data, when a band's software is too old for Method 1, and for the finger labeler. You need the Studio running first. In the wizard it is the button **Use the band's own Wi-Fi instead**, on the **Plug the band into this computer** step.

1. **Plug the band into USB, turn it on, and find its name and password on the sticker.** Plug the USB-C cable into the band and into a USB power source (a laptop port or a phone charger) and leave it plugged in until setup is finished. Look at the light. A band that is powered always shows a **faint glow** (it can look white or yellow); a dark light means the band is switched off. If the light is dark, press the power button at the top edge once: the faint glow tells you that the press switched the band on. A band that has never been set up goes straight to setup mode. You should see its light blink **blue, two quick blinks then a pause**. For a band without a screen, that is the normal setup pattern and not a fault.

   Find the sticker on the band. It has lines like these (yours will differ):

   ```
   Setup Wi-Fi      OM-flexgrid-d7af0b
   Setup password   kd3mpq72xy
   ```

   The password is ten lowercase letters and digits, never the letters `l` or `o` and never the digits `0` or `1`. The name and password are different on every band and do not change when you set up your own Wi-Fi.

   If your band does have a screen, it shows the same thing and its light blinks blue once a second:

   ```
   WIFI SETUP
   OM-flexgrid-
   d7af0b
   PW kd3mpq72xy
   ```

   The network name is long, so the screen splits it over two lines: read them together as `OM-flexgrid-d7af0b`. The last line, after `PW`, is the password.

   Only the faint glow, and it never blinks blue? Check that the USB cable is plugged in at both ends (try another cable or port), because on battery alone the band cannot start its setup network. If the light is dark, press the power button once. If the band shows green or another pattern instead, it already has a Wi-Fi saved: hold the **MENU** and **SELECT** buttons together for 3 seconds (see [Starting over](#starting-over) below).

2. **Open the wizard.** In the Studio, click **Set up a device** in the top right. (Or open `http://localhost:8000/setup`; use the address your start-up window printed if it is not 8000.)

3. **Get ready.** Read the short checklist (it repeats the section above) and click **I am ready**.

4. **What are you setting up?** Click **A band**, then **Use the band's own Wi-Fi instead**. (For a finger labeler, click **A finger labeler (optional)** and you arrive at the next step directly.)

5. **Join the device's own Wi-Fi.** The page shows the steps for your computer. In short:
   - **Windows:** click the Wi-Fi icon at the bottom right, choose the network starting with `OM-`, and connect.
   - **Mac:** click the Wi-Fi icon in the bar at the top of the screen and choose the network starting with `OM-`.
   - **Linux:** open the network menu, choose Wi-Fi, then the network starting with `OM-`.

   Type the **Setup password** from the sticker (on a band with a screen, the part after `PW`). When your computer is connected, the page shows a green tick and **Found Band flexgrid-d7af0b**. It asks **Does the name end with d7af0b?** Check the last six characters against the **Setup Wi-Fi** line on the sticker and click **Yes, that is the one**. (If several bands are in setup mode at once, turn the others off so only one answers.)

6. **Choose your Wi-Fi.** Pick your network from the list the band can see (only 2.4 GHz networks are listed), or choose **Another network (type it)** and type the name exactly, including capital letters and spaces. Type the password; **Show** lets you check it. Leave **Remember for the next device (this browser tab only)** ticked if you have a second band to set up: the details are kept only in this browser tab and never saved on disk. Click **Send to the device**.

7. **Reconnect your computer.** The page says **Saved. The device is restarting (about 20 seconds). Now reconnect this computer to "your Wi-Fi name".** Join your normal Wi-Fi again from the Wi-Fi menu. The page shows a checklist that ticks off as the band restarts, your computer comes back, and the band appears.

8. **Done.** You should see **Band flexgrid-d7af0b is online at 192.168.x.x** (the numbers are the band's address on your network; you never need to type them). The band's light is green for two seconds, then steady orange once your computer is receiving its readings (a band with a screen also shows `WIFI OK` and the same address). From now on the band also runs on battery alone. Click **Set up the next device** for the second band, or **Go to the live view**.

9. **Tidy up.** In your computer's Wi-Fi list, tell it to forget the `OM-` network so it does not reconnect to the band by mistake:
   - **Windows:** Settings, Network and internet, Wi-Fi, Manage known networks, choose the `OM-` network, **Forget**.
   - **Mac:** System Settings, Wi-Fi, click the details button next to the `OM-` network, then **Forget This Network**.
   - **Linux:** open the network settings, select the `OM-` connection and remove it.

Repeat from step 1 for the second band. It makes its own `OM-flexgrid-...` network with a different name and password.

### By hand, from any computer or phone

You can skip the wizard. This works from a phone too, which is handy if your computer is busy.

1. Plug the band into USB, turn it on and find its name and password on the sticker as in Method 2, step 1. Keep it plugged in until it has joined.
2. On the computer or phone, join the `OM-flexgrid-...` network with the **Setup password** from the sticker. (On a phone, if it asks whether to stay connected to a network with no internet, say yes. Turning off mobile data for a minute helps.)
3. Open a browser and type exactly `http://192.168.4.1` (use `http`, not `https`). The page is titled **OpenMuscle: connect to Wi-Fi**.
4. Fill in **Wi-Fi name (SSID)** and **Wi-Fi password**, and press **Connect**. The page says **Saved. Device is rebooting; reconnect to your home Wi-Fi.**
5. Reconnect to your usual Wi-Fi and wait about 30 seconds. The band's light turns green for two seconds, and a band with a screen shows `WIFI OK`.

The band shows up in the Studio by itself within about half a minute.

## If the band does not appear within a minute

The wizard shows an ordered list of things to try. The usual reasons, most likely first:

1. **Wrong password.** The band goes back to setup mode by itself after about 30 seconds (its light returns to blue; a band with a screen shows `WIFI SETUP` and adds `LAST TRY FAILED` and a reason, see the table below). Start the wizard again and retype the password with **Show** turned on. With Method 1, click **Start over with this device**: it takes you back to the USB step.
2. **The network is 5 GHz only, or asks you to sign in.** Use your phone as a hotspot.
3. **Your computer joined a different network** from the band. Check the Wi-Fi icon.
4. **Your computer's firewall** is blocking the band. See [troubleshooting.md](troubleshooting.md).
5. **Your router keeps devices apart** ("client isolation", "AP isolation" or a guest network). Use the main network.
6. The wizard's **Run a check** and **Find my devices** buttons, or typing the band's address (you can read it from your router's list of devices), help in the last cases.

When a band fails to join, its light shows an amber fast blink and then the blue setup pattern again. A band without a screen cannot say why, so go through the list above in order. A band with a screen tells you why:

| Band screen (if fitted) | Meaning | What to do |
|---|---|---|
| `WIFI FAILED` then `check password` | The password was rejected | Retype it carefully |
| `WIFI FAILED` then `network not found or 5 GHz` | The name was misspelled, the router is out of range, or the network is 5 GHz only | Check the name, move closer, or use a 2.4 GHz network |
| `WIFI FAILED` then `join timed out` | The router never answered | Check the router, then try again |

A failed first join shows an amber fast blink for about 2 seconds, then the band restarts into setup mode (blue blink) with the same network name and password as before. The Wi-Fi password you typed is erased from the band, so you start the wizard again.

## Starting over

To make a band forget its Wi-Fi and go back to setup mode (you changed networks, or typed the wrong one and it joined something else):

1. Keep the band plugged into USB (setup mode does not start on battery alone), and hold the **MENU** and **SELECT** buttons together.
2. The light blinks magenta. If you let go now, the Wi-Fi settings are wiped.
3. Keep holding for 3 seconds, then let go. The band restarts into setup mode (blue light; a band with a screen shows `WIFI SETUP`). Its network name and password are the same as before, and still match the sticker.

If you let go early, nothing happens. Releasing one button before the 3 seconds are up does nothing, and the other button is ignored until both are up.

You do not have to start over to change networks with Method 1: send the new Wi-Fi details over the USB cable and they replace the old ones.

## The optional finger labeler (LASK5)

The LASK5 is not part of the starter kit. If you have one, it is set up through its own network (Method 2): the wizard has a **A finger labeler (optional)** choice, the labeler shows its own `WIFI SETUP` screen with its network name and password, and you join its `OM-` network. To put it back into setup mode, open its menu and choose **Reset Wi-Fi**.

## Privacy

With Method 1 the Wi-Fi password goes down the USB cable straight to the band, through the OpenMuscle program on your computer. With Method 2 it is sent only to the band, over the band's own temporary network, through the same program. Either way it is not sent to the internet, and OpenMuscle does not save it or write it to its logs. The band keeps it in its own memory, and holding MENU and SELECT for 3 seconds erases it.

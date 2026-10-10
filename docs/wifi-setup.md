# Put your band on Wi-Fi

A new band does not know your Wi-Fi yet. This page walks you through telling it, once, so it can join your network and send its readings to your computer. It takes about five minutes per band. You do not need to type any commands.

If you are still at the start of the kit journey, go back to [getting-started.md](getting-started.md). If a step here does not behave as described, see [troubleshooting.md](troubleshooting.md).

## What is happening

Until it knows your Wi-Fi, a band makes a small Wi-Fi network of its own, named like `OM-flexgrid-d7af0b`. You join that network from your computer for a minute, type the name and password of your own Wi-Fi into a page the band shows, and the band restarts and joins your Wi-Fi. After that you put your computer back on your usual Wi-Fi, and the band shows up in the Studio.

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

## What you will notice

While your computer is joined to the band's `OM-` network it **has no internet**. Web pages stop loading and your computer may say "no internet" or "connected, no internet". That is normal and lasts only a few minutes. The setup page keeps working because it runs on your own computer.

## Method 1: the setup wizard in the Studio (recommended)

You need the Studio running first (see [getting-started.md](getting-started.md), step 3). The wizard works only in the browser on the computer that runs OpenMuscle.

1. **Turn the band on.** A band that has never been set up goes straight to setup mode. You should see this on its small screen, and its light blinking blue about once a second:

   ```
   WIFI SETUP
   OM-flexgrid-
   d7af0b
   PW kd3mpq72xy
   ```

   The first line changes every few seconds (it also shows "see guide"). The network name is long, so it is split over two lines: read them together as `OM-flexgrid-d7af0b`. The last line, after `PW`, is the band's temporary password: ten lowercase letters and digits, never the letters `l` or `o` and never the digits `0` or `1`. Your kit may also have a label with the same name and password.

   Not showing that? The band already has a Wi-Fi saved. Hold the **MENU** and **SELECT** buttons together for 3 seconds (see [Starting over](#starting-over) below).

2. **Open the wizard.** In the Studio, click **Set up a device** in the top right. (Or open `http://localhost:8000/setup`; use the address your start-up window printed if it is not 8000.)

3. **Get ready.** Read the short checklist (it repeats the section above) and click **I am ready**.

4. **What are you setting up?** Click **A band**.

5. **Join the device's own Wi-Fi.** The page shows the steps for your computer. In short:
   - **Windows:** click the Wi-Fi icon at the bottom right, choose the network starting with `OM-`, and connect.
   - **Mac:** click the Wi-Fi icon in the bar at the top of the screen and choose the network starting with `OM-`.
   - **Linux:** open the network menu, choose Wi-Fi, then the network starting with `OM-`.

   Type the password from the last line of the band's screen (the part after `PW`). When your computer is connected, the page shows a green tick and **Found Band flexgrid-d7af0b**. It asks **Does the screen end with d7af0b?** Check the last six characters against the band's screen and click **Yes, that is the one**. (If several bands are in setup mode at once, turn the others off so only one answers.)

6. **Choose your Wi-Fi.** Pick your network from the list the band can see (only 2.4 GHz networks are listed), or choose **Another network (type it)** and type the name exactly, including capital letters and spaces. Type the password; **Show** lets you check it. Leave **Remember for the next device (this browser tab only)** ticked if you have a second band to set up: the details are kept only in this browser tab and never saved on disk. Click **Send to the device**.

7. **Reconnect your computer.** The page says **Saved. The device is restarting (about 20 seconds). Now reconnect this computer to "your Wi-Fi name".** Join your normal Wi-Fi again from the Wi-Fi menu. The page shows a checklist that ticks off as the band restarts, your computer comes back, and the band appears.

8. **Done.** You should see **Band flexgrid-d7af0b is online at 192.168.x.x** (the numbers are the band's address on your network; you never need to type them). The band's screen shows `WIFI OK` and the same address, and its light is green for two seconds, then steady orange once your computer is receiving its readings. Click **Set up the next device** for the second band, or **Go to the live view**.

9. **Tidy up.** In your computer's Wi-Fi list, tell it to forget the `OM-` network so it does not reconnect to the band by mistake:
   - **Windows:** Settings, Network and internet, Wi-Fi, Manage known networks, choose the `OM-` network, **Forget**.
   - **Mac:** System Settings, Wi-Fi, click the details button next to the `OM-` network, then **Forget This Network**.
   - **Linux:** open the network settings, select the `OM-` connection and remove it.

Repeat from step 1 for the second band. It makes its own `OM-flexgrid-...` network with a different name and password.

### If the band does not appear within a minute

The wizard shows an ordered list of things to try. The usual reasons, most likely first:

1. **Wrong password.** The band goes back to `WIFI SETUP` by itself after about 30 seconds and its top line adds `LAST TRY FAILED` and a reason (see the table below). Start the wizard again and retype the password with **Show** turned on.
2. **The network is 5 GHz only, or asks you to sign in.** Use your phone as a hotspot.
3. **Your computer joined a different network** from the band. Check the Wi-Fi icon.
4. **Your computer's firewall** is blocking the band. See [troubleshooting.md](troubleshooting.md).
5. **Your router keeps devices apart** ("client isolation", "AP isolation" or a guest network). Use the main network.
6. The wizard's **Run a check** and **Find my devices** buttons, or typing the band's address (you can read it from your router's list of devices), help in the last cases.

When a band fails to join, its screen and light tell you why:

| Band screen | Meaning | What to do |
|---|---|---|
| `WIFI FAILED` then `check password` | The password was rejected | Retype it carefully |
| `WIFI FAILED` then `network not found or 5 GHz` | The name was misspelled, the router is out of range, or the network is 5 GHz only | Check the name, move closer, or use a 2.4 GHz network |
| `WIFI FAILED` then `join timed out` | The router never answered | Check the router, then try again |

A failed first join shows an amber fast blink for about 2 seconds, then the band restarts into setup mode (blue blink) with the same network name and password as before. The Wi-Fi password you typed is erased from the band, so you start the wizard again.

## Method 2: by hand, from any computer or phone

You can skip the wizard. This works from a phone too, which is handy if your computer is busy.

1. Turn the band on and read its screen as in Method 1, step 1.
2. On the computer or phone, join the `OM-flexgrid-...` network with the password after `PW`. (On a phone, if it asks whether to stay connected to a network with no internet, say yes. Turning off mobile data for a minute helps.)
3. Open a browser and type exactly `http://192.168.4.1` (use `http`, not `https`). The page is titled **OpenMuscle: connect to Wi-Fi**.
4. Fill in **Wi-Fi name (SSID)** and **Wi-Fi password**, and press **Connect**. The page says **Saved. Device is rebooting; reconnect to your home Wi-Fi.**
5. Reconnect to your usual Wi-Fi and wait about 30 seconds. The band's screen shows `WIFI OK`.

The band shows up in the Studio by itself within about half a minute.

## Starting over

To make a band forget its Wi-Fi and go back to setup mode (you changed networks, or typed the wrong one and it joined something else):

1. Hold the **MENU** and **SELECT** buttons together.
2. The light blinks magenta. If you let go now, the Wi-Fi settings are wiped.
3. Keep holding for 3 seconds, then let go. The band restarts into setup mode and shows `WIFI SETUP` again. Its network name and password are the same as before.

If you let go early, nothing happens. Releasing one button before the 3 seconds are up does nothing, and the other button is ignored until both are up.

## The optional finger labeler (LASK5)

The LASK5 is not part of the starter kit. If you have one, it is set up the same way: the wizard has a **A finger labeler (optional)** choice, the labeler shows its own `WIFI SETUP` screen, and you join its `OM-` network. To put it back into setup mode, open its menu and choose **Reset Wi-Fi**.

## Privacy

The Wi-Fi password is sent only to the band, over the band's own temporary network, through the OpenMuscle program on your computer. It is not sent to the internet, and OpenMuscle does not save it or write it to its logs. The band keeps it in its own memory, and holding MENU and SELECT for 3 seconds erases it.

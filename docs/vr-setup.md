# OpenMuscle VR — setup & operation

Wear FlexGrid + a Meta Quest 3S together. The headset's hand tracking becomes the ground-truth label source for training a muscle-signal → finger-pose model. The desktop OpenMuscle UI is unchanged; this guide just adds the VR companion.

Companion code: [`pc/src/openmuscle/web/static/vr/`](../pc/src/openmuscle/web/static/vr/) (client) and [`pc/src/openmuscle/web/app.py`](../pc/src/openmuscle/web/app.py) (server endpoints). The Quest is integrated as a synthetic device with `device_type: "quest_hand"` — see [`protocol.md`](protocol.md) for the wire format.

## Why HTTPS is non-optional

Quest Browser refuses to grant WebXR hand-tracking outside a secure context. You have two ways to get one:

| Mode | Pros | Cons |
|---|---|---|
| **HTTPS over LAN** via mkcert | untethered, real use case | one-time cert install on the headset |
| **`adb reverse` over USB** (localhost) | zero certs, fast for debugging | cable on the headset distorts natural arm motion |

Day-to-day capture work should use HTTPS. The USB fallback is for proving the loop end-to-end before you've finished cert setup.

## One-time setup

### 1. mkcert on the PC

```powershell
# Windows (choco). On macOS: `brew install mkcert`. On Linux: distro package.
choco install mkcert

mkcert -install                       # installs the mkcert root CA into Windows
mkcert -cert-file vr-cert.pem `
       -key-file  vr-key.pem `
       192.168.1.42 localhost          # <-- replace with your PC's LAN IP
```

The two PEM files land in your current directory. Keep them next to the captures dir, or anywhere the `openmuscle web` command can read them.

### 2. Install the mkcert root CA on the Quest

**Heads up:** this is the painful step. Meta's Horizon OS consumer shell **hides** the standard Android "Settings → Security → Install a certificate" path. The Settings panel you see when tapping the gear icon in VR is Meta's Horizon Settings, not AOSP Settings — different surface, no cert-install button. You have to launch the AOSP Settings activity via ADB.

Procedure validated on Quest 3S, Horizon OS as of May 2026:

```powershell
# (a) Push the root CA from your PC to the headset's Downloads folder
adb push "$env:LOCALAPPDATA\mkcert\rootCA.pem" /sdcard/Download/openmuscle-vr-rootCA.pem

# (b) Launch the AOSP Security Dashboard activity directly via ADB.
#     The $ MUST be backslash-escaped through PowerShell single-quotes so it
#     survives BOTH the host shell AND the Android shell. Without the escape,
#     $ gets eaten as a variable expansion and the activity name truncates to
#     just '.Settings' which is not exported (Permission Denial).
adb shell 'am start -n com.android.settings/.Settings\$SecurityDashboardActivity'
```

On the Quest after running those:

1. **The 2D Settings panel may not appear immediately in your view.** Horizon shell aggressively suppresses unfamiliar 2D activities. **Press the Meta button** on your right controller, look for "Settings" (or similar) in the universal-menu app switcher, and click to bring it forward.
2. In the AOSP Security dashboard: **Trusted credentials** → **Encryption** section → **Install a certificate** → **CA certificate**.
3. **"Your data won't be private"** warning → tap **Install anyway** (your own root CA, the warning is generic Android boilerplate).
4. **If no screen-lock PIN is set**, Quest refuses and prompts you to set one. Set a PIN, then re-do step 2.
5. **File picker gotcha:** defaults to "Recent" view which is **empty** on a fresh install. Tap the **hamburger menu (≡)** at the top of the picker → navigate to **Internal Storage → Download** → tap **openmuscle-vr-rootCA.pem**.
6. Success: "CA certificate installed" or similar.

You only do this once per headset. The CA stays trusted across Horizon OS updates.

**Fallback if you can't find the AOSP Settings panel in the app switcher:** open the headset's **Files** app, navigate to **Download/openmuscle-vr-rootCA.pem**, tap it. Some Horizon versions trigger the install dialog from the file-open intent directly.

**Activities that work on Horizon OS** (useful for future Quest dev when standard menus are hidden):
- `com.android.settings/.Settings$SecurityDashboardActivity` — security menu (the one you just used)
- `com.android.settings/.Settings$TrustedCredentialsSettingsActivity` — view installed certs (for verifying yours is there)
- `com.android.settings/.security.CredentialStorage` — direct install activity, but Horizon often suppresses its panel
- `com.android.settings/.Settings$NetworkDashboardActivity`
- `com.android.settings/.Settings$DevelopmentSettingsDashboardActivity`

**Activities that DON'T exist on Horizon** (don't try — Error type 3):
- `com.android.settings/.Settings$SecuritySettingsActivity` (older Android name, removed)
- `com.android.settings/.Settings$EncryptionAndCredentialActivity` (renamed)

**Non-exported, can't launch via ADB** (Permission Denial from null uid):
- `com.android.settings/.Settings` (the bare main activity)

### 3. Start the server with HTTPS

`openmuscle web` AUTO-LOADS the TLS pair when it's configured, so you do NOT have
to pass `--ssl-*` every time. To configure it once for any directory, drop the
mkcert pair into `~/.openmuscle/` (on Windows: `C:\Users\<you>\.openmuscle\`):

```powershell
mkdir $HOME\.openmuscle -Force
copy vr-cert.pem,vr-key.pem $HOME\.openmuscle\
```

Then, from anywhere:

```powershell
openmuscle web
```

You'll see:

```
OpenMuscle web UI: https://localhost:8000
Listening for devices on UDP 3141
TLS: cert=...\vr-cert.pem  key=...\vr-key.pem  (from ~/.openmuscle/)
WebXR URL for the Quest: https://<your-LAN-ip>:8000/vr?mode=ar&arm=both
```

Search order (first match wins): explicit `--ssl-certfile/--ssl-keyfile` →
`OPENMUSCLE_SSL_CERTFILE`/`OPENMUSCLE_SSL_KEYFILE` env vars → `~/.openmuscle/` →
the current directory (`vr-cert.pem` + `vr-key.pem`). If NO pair is found,
`openmuscle web` serves plain HTTP and prints a notice telling you WebXR needs
HTTPS and how to fix it. `start-vr-https.bat` (which passes the flags explicitly
from `pc/`) still works too.

## Per-session flow

1. **Boot FlexGrid** — it joins your Wi-Fi and starts streaming UDP to port 3141. Confirm in the desktop UI's Devices panel.
2. **Open `/vr` in Quest Browser** at `https://<your-LAN-ip>:8000/vr`. (The desktop UI lives at `/`; the VR companion at `/vr`.)
3. The landing page runs three preflight checks. All three should be green checkmarks:
   - HTTPS / secure context
   - WebXR + immersive-vr supported
   - Server reachable
4. **Pick the FlexGrid arm** in the dropdown (`right` is default). Only the joints of this hand get pushed to `/ws/quest`. The other hand stays free for the record button.
5. Tap **Enter VR**. Grant hand-tracking when Quest asks.
6. A floating panel ~70cm in front shows the live FlexGrid heatmap. Header strip above it shows per-device Hz when idle, or `REC · N rows · match X%` when recording.
7. Hold your captured hand up; you'll see small blue spheres on each tracked joint.
8. **Start recording**:
   - **Off-hand button**: tap the gray sphere below the heatmap with your other hand's index finger. It turns red.
   - **Pinch gesture**: pinch index + thumb on the captured hand and hold for ~1 second. A yellow ring on the index tip fills during the hold; release triggers the toggle.
9. Do your gesture set. For the first FlexGrid V3 training run we focus on **index / middle / ring / pinky finger curls** — the thumb is driven by intrinsic hand muscles the FlexGrid can't see, so it's excluded.
10. **Stop recording** the same way.
11. Take the headset off. The capture is in `data/raw/merged/`:

    ```
    capture_<ts>.csv                  # paired (sensor + 182 joint floats)
    capture_<ts>.sensor.jsonl         # raw FlexGrid packets
    capture_<ts>.label.jsonl          # raw Quest packets
    capture_<ts>.labels.schema.json   # column -> (joint, channel) map
    capture_<ts>.meta.json            # auto.label_source="quest_hand" + your tags
    ```

12. **Train**: in the desktop UI's Captures panel, tick your new capture(s) and hit **Train selected**. Or from CLI: `openmuscle train data/raw/merged/capture_<ts>.csv`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Enter VR" button stays disabled | Page opened in the in-headset Library panel, not Quest Browser | Open Quest Browser app explicitly; navigate to the URL |
| "INSECURE (http:)" in the HTTPS checkmark | Reached the page over plain HTTP | Either switch to `https://` (LAN + mkcert) or use `adb reverse` + `http://localhost:8000/vr` |
| Cert install on Quest fails silently | Quest doesn't have a screen-lock PIN | Settings → Security → Lock screen → set a PIN, then re-try CA install |
| Heatmap stays "waiting for FlexGrid…" inside VR | FlexGrid not streaming, or wrong UDP port | Open the desktop UI; verify the device is in the Devices panel and packets > 0 |
| `match_rate` is very low (< 30%) in recording header | Quest WS connection dropping, or sensor and label streams have wildly different rates | Check WS reconnect logs in the desktop UI's Log panel; try increasing `window_ms` via the recording start body if needed (default for Quest is 175 ms) |
| Joints visible but no `quest-<arm>` device in the Devices panel | `/ws/quest` failed to connect (e.g. CORS, cert mismatch) | Check the Quest Browser dev-tools (chrome://inspect on a connected PC) for WS errors |

## Architecture, in one diagram

```
                            ┌──────────────────────┐
                            │   Meta Quest 3S       │
                            │                       │
   ┌───────────┐            │  Quest Browser /vr   │
   │ FlexGrid  │            │    │                 │
   │ (ESP32-   │   UDP      │    │ WebXR XRHand    │
   │  S3 WiFi) │ ─────────→ │    │ sample joints   │
   └───────────┘   :3141    │    ▼                 │
        ▲                   │  WS /ws/quest        │
        │                   │    │                 │
        │                   │    │   WS /ws/live   │
        │                   │    │   ◀──── heatmap │
        │                   │    │                 │
        │                   └────┼─────────────────┘
        │                        │
        │                        ▼
        │            ┌──────────────────────────┐
        │            │  PC: openmuscle web      │
        │            │                          │
        │  UDP       │   UDPListener  ─→ ┐      │
        └────────────┤                   │      │
                     │   ingest_quest    ─→ AppState   ─→ paired CSV
                     │   (synthesizes a    matcher        + JSONL +
                     │    quest_hand       recorder       labels.schema.json
                     │    packet)                         + meta.json
                     │                                    │
                     │   /ws/live ◀── snapshot ───────────┘
                     └──────────────────────────┘
```

The whole integration trick is that `ingest_quest_packet` synthesizes an `OpenMusclePacket` of type `quest_hand` in-process, then hands it to `_handle_packet` — the **same** function the UDP listener feeds into. From the recorder's view the Quest is just another device. Zero special-casing downstream.

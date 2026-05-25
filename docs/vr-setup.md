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

```powershell
# Find the root CA
mkcert -CAROOT
# -> C:\Users\<you>\AppData\Local\mkcert
```

Copy `rootCA.pem` from that folder to the headset. Two options:

- **USB**: connect Quest via cable, in Quest Settings allow file transfer, drag-drop `rootCA.pem` onto the headset's internal storage (e.g. `Download/`).
- **Email/Drive**: email it to yourself or upload to Google Drive; open on the headset.

On the Quest:

1. Settings → **Security** → More Security Settings → **Install a certificate** → **CA certificate**.
2. Confirm the "you're sure?" prompt (yes — it's your own root CA).
3. Browse to where you dropped `rootCA.pem` and pick it.
4. The Quest will prompt for / set a screen-lock PIN if you don't already have one — required for CA install.

You only do this once per headset.

### 3. Start the server with HTTPS

```powershell
cd D:\path\to\OpenMuscle-Software\pc
pip install -e .                       # first time only

openmuscle web --ssl-certfile vr-cert.pem `
               --ssl-keyfile  vr-key.pem
```

You'll see:

```
OpenMuscle web UI: https://localhost:8000
Listening for devices on UDP 3141
TLS: cert=vr-cert.pem  key=vr-key.pem
WebXR URL for the Quest: https://<your-LAN-ip>:8000/vr
```

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

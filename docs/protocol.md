# OpenMuscle Packet Protocol v1.0

OpenMuscle devices send their readings as **JSON-encoded UTF-8** packets over **UDP**. The computer receives them on **UDP port 3141** (sensor and label frames). V4 devices also announce themselves on **UDP port 3140** and are subscribed to over a TCP command channel; see [Discovery and subscription](#discovery-and-subscription-v4-devices). Devices that need a computer's address in their settings (older firmware) send to it directly.

## Packet Schema

```json
{
    "v": "1.0",
    "type": "flexgrid",
    "id": "fg-01",
    "ts": 164587,
    "data": { ... },
    "meta": { ... }
}
```

### Required Fields

| Field  | Type   | Description |
|--------|--------|-------------|
| `v`    | string | Protocol version (`"1.0"`) |
| `type` | string | Device type: `"flexgrid"`, `"lask5"`, or custom |
| `id`   | string | Unique device identifier (user-configurable) |
| `ts`   | int    | Device-local timestamp in milliseconds |
| `data` | object | Device-specific sensor payload (see below) |

### Optional Fields

| Field  | Type   | Description |
|--------|--------|-------------|
| `meta` | object | Battery level, calibration state, RSSI, firmware version |

## Device-Specific Data Payloads

### FlexGrid (`type: "flexgrid"`)

```json
{
    "matrix": [[col0_row0, col0_row1, col0_row2, col0_row3], ...],
    "rows": 4,
    "cols": 15,
    "imu": {"gyro": [gx, gy, gz], "accel": [ax, ay, az]}
}
```

`matrix` is a list of columns, each a list of 4 row values: 15 columns x 4 rows = 60 cells for the V4 band (the first FlexGrid used 16 columns), raw ADC values (0-4095). The computer flattens it row-major into the CSV columns `R0C0` to `R3C14`. `imu` is present on V4 bands (raw gyro and accel counts; the per-chip scale is advertised in the band's announce and `get_info` reply). Battery, uptime and similar status values arrive in `meta` about once a second.

### LASK5 (`type: "lask5"`)

```json
{
    "values": [v0, v1, v2, v3],
    "joystick": {"x": 2048, "y": 2048}
}
```

4 piston sensor values. Joystick is optional.

### Quest hand tracking (`type: "quest_hand"`)

Synthesized server-side from WebSocket frames sent by the WebXR client at `/vr` (browsers can't speak UDP). The payload represents one tracked hand sampled from `XRHand` each XRFrame:

```json
{
    "values":     [px,py,pz, rx,ry,rz,rw,  ...]    // flat, 7 floats per joint
    "handedness": "left" | "right",
    "joint_names": ["wrist", "thumb-metacarpal", ...],
    "hands": {
        "handedness": "left" | "right",
        "joints": [
            {"name": "wrist", "pos": [x,y,z], "rot": [x,y,z,w], "radius": 0.02, "valid": true},
            ...
        ]
    }
}
```

- `values` follows the same convention as LASK5: flat, in canonical joint × channel order, so the recorder and matcher pair `quest_hand` frames with FlexGrid frames identically to LASK5. The order is `[px, py, pz, rx, ry, rz, rw] × N joints`.
- **Fixed-length contract.** A well-behaved client MUST send all joints in canonical `joint_names` order every frame, so the `values` length and per-slot meaning are stable across frames. When an individual joint pose is momentarily unavailable, send a zero position + identity quaternion with `"valid": false` rather than omitting the joint, because omitting it would shift every later joint into the wrong CSV column and silently misalign the labels. The server defends against violations by locking the label width at the first label packet and padding/truncating later rows to keep the CSV rectangular (it counts and logs any mismatch), but clients should not rely on that.
- `valid` (per joint, in `hands.joints`) marks whether the pose was actually tracked this frame. It is preserved in the JSONL sidecar so offline analysis can filter zero-filled joints; the flat `values` / CSV carry only the numbers.
- `joint_names` lists the joints in the same order they're flattened into `values`. The standard set is the W3C WebXR Hand Input spec (25 joints: wrist + 4 thumb + 5 each for index/middle/ring/pinky).
- `hands` is the structured per-joint form, kept in the JSONL sidecar for offline analysis. It's redundant with `values` + `joint_names` but easier to diff by eye.
- Empty payloads (the headset reports tracking-lost for the whole hand this frame) are dropped silently by the server, because they would otherwise produce zero-rows that mislead training.

A per-capture `<name>.labels.schema.json` sidecar is written on the first `quest_hand` packet of a recording. It maps `label_0..label_N` columns in the CSV back to `(joint, channel)`, so the wide label vector is self-describing.

**Future-proofing note:** v1 captures one hand per recording (`handedness` is a single `"left"` or `"right"` string). A future `handedness: "both"` extension (payload carries both hands in `data.values` with the schema sidecar growing a parallel `joint_names_left` / `joint_names_right`, or a per-column `hand` field) would be backward-compatible. Old consumers see a wider but still-flat values vector; new consumers can split it via the schema. Don't accidentally close that door in any future refactor of `_flatten_quest_joint` / `_write_labels_schema`.

### Webcam hand tracking (`type: "webcam_hand"`)

The camera page tracks one hand in the browser and sends 25 joints in the same layout as `quest_hand` to the server's `/ws/webcam` WebSocket, where they become `webcam_hand` packets (device ids `webcam-left` and `webcam-right`). Positions are in a hand-local frame and rotations are identity. See [webcam-labeling.md](webcam-labeling.md#what-is-recorded).

### Discovery and subscription (V4 devices)

V4 bands and the LASK5 do not need the computer's address. Instead:

1. **Announce.** While no computer is subscribed, the device broadcasts an `announce` packet on UDP port 3140 about once a second. It carries the device id, type (`dev`), firmware, the ports it offers (`services`: the data port and the command port) and its capabilities. The computer can also broadcast `{"verb": "discover"}` to port 3140 and every device answers at once.
2. **Subscribe.** The computer connects to the device's TCP command port (8001 for a band, 8002 for a LASK5) and sends a `subscribe` command, then a `heartbeat` about once a second. A device drops a subscriber after about 5 seconds of silence. One JSON object per line.
3. **Stream.** The device sends its frames by UDP to every subscribed computer, on the computer's data port (3141). Other commands include `get_info` and `set_role`.
4. **Setup mode.** A device with no Wi-Fi saved offers its own network `OM-...` and a setup page at `http://192.168.4.1` instead (see [wifi-setup.md](wifi-setup.md)).

The full specification lives in the OpenMuscle-Connect repository (`DEVICE-DISCOVERY-SPEC.md` and `WIRE-FORMAT.md`); the code that implements the computer side is `pc/src/openmuscle/discovery/manager.py`.

### Adding a New Device Type

Define a new `type` string and document the `data` shape. The PC-side parser auto-discovers devices by their `type` field.

## Versioning policy

Adding a new `type` string (e.g. `quest_hand`) is **non-breaking under v1.0**: existing parsers ignore unknown types, the schema envelope is unchanged, and devices that don't speak the new type are unaffected. Bump the `"v"` field to `"1.1"` (or beyond) only when changing the envelope itself (required-field set, semantics of `ts`/`id`, etc.).

## Backward Compatibility

The PC parser (`openmuscle.protocol.parser`) auto-detects three formats:
1. **New protocol**: JSON object with `"v"` field
2. **Legacy FlexGrid**: bare JSON array (16x4 matrix)
3. **Legacy LASK5/SensorBand**: Python dict with `"id"` field (parsed via `ast.literal_eval`)

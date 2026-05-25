# OpenMuscle Packet Protocol v1.0

All OpenMuscle devices communicate over **UDP port 3141** using **JSON-encoded UTF-8** packets.

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
    "cols": 16
}
```

16 columns x 4 rows of ADC values (0-4095).

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
            {"name": "wrist", "pos": [x,y,z], "rot": [x,y,z,w], "radius": 0.02},
            ...
        ]
    }
}
```

- `values` follows the same convention as LASK5 — flat, in canonical joint × channel order — so the recorder and matcher pair `quest_hand` frames with FlexGrid frames identically to LASK5. The order is `[px, py, pz, rx, ry, rz, rw] × N joints`.
- `joint_names` lists the joints in the same order they're flattened into `values`. The standard set is the W3C WebXR Hand Input spec (25 joints: wrist + 4 thumb + 5 each for index/middle/ring/pinky).
- `hands` is the structured per-joint form, kept in the JSONL sidecar for offline analysis. It's redundant with `values` + `joint_names` but easier to diff by eye.
- Empty payloads (the headset reports tracking-lost for the whole hand this frame) are dropped silently by the server — they'd otherwise produce zero-rows that mislead training.

A per-capture `<name>.labels.schema.json` sidecar is written on the first `quest_hand` packet of a recording. It maps `label_0..label_N` columns in the CSV back to `(joint, channel)`, so the wide label vector is self-describing.

### Adding a New Device Type

Define a new `type` string and document the `data` shape. The PC-side parser auto-discovers devices by their `type` field.

## Backward Compatibility

The PC parser (`openmuscle.protocol.parser`) auto-detects three formats:
1. **New protocol**: JSON object with `"v"` field
2. **Legacy FlexGrid**: bare JSON array (16x4 matrix)
3. **Legacy LASK5/SensorBand**: Python dict with `"id"` field (parsed via `ast.literal_eval`)

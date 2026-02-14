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

### Adding a New Device Type

Define a new `type` string and document the `data` shape. The PC-side parser auto-discovers devices by their `type` field.

## Backward Compatibility

The PC parser (`openmuscle.protocol.parser`) auto-detects three formats:
1. **New protocol**: JSON object with `"v"` field
2. **Legacy FlexGrid**: bare JSON array (16x4 matrix)
3. **Legacy LASK5/SensorBand**: Python dict with `"id"` field (parsed via `ast.literal_eval`)

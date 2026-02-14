# Device Template

Use this template to create a new OpenMuscle device.

## Steps

1. Copy this `_template/` folder and rename it to your device name (e.g., `my_sensor_v1/`)
2. Edit `device.py`:
   - Rename `MyDevice` and `MySensor` to your device/sensor names
   - Set `DEVICE_TYPE` to a unique string (e.g., `"my_sensor"`)
   - Set `DEVICE_DEFAULTS` with your device's default configuration
   - Implement `MySensor.read()` to return your sensor data as a dict
   - Implement `MyDevice.run()` with your async task loops
3. Edit `boot.py` to import your device class
4. Edit `config/defaults.json` to match your `DEVICE_DEFAULTS`
5. Flash to your ESP32:

```bash
# Install shared library
mpremote mip install ssd1306
mpremote cp ../../lib/om_*.py :/lib/

# Upload device files
mpremote cp boot.py device.py :/
mpremote mkdir :/config
mpremote cp config/defaults.json :/config/
```

## Packet Format

Your device will automatically send packets in the standard format:

```json
{
    "v": "1.0",
    "type": "my_device",
    "id": "my-device-01",
    "ts": 12345,
    "data": { ... your sensor data ... }
}
```

The PC-side `openmuscle` CLI will auto-discover your device by its `type` field.

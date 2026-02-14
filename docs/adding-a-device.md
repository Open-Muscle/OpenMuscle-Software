# Adding a New Device

This guide walks through creating firmware for a new OpenMuscle sensor device.

## Prerequisites

- ESP32-S3 with MicroPython flashed
- `mpremote` installed (`pip install mpremote`)
- SSD1306 library installed on device (`mpremote mip install ssd1306`)

## Steps

### 1. Copy the Template

```bash
cp -r embedded/devices/_template embedded/devices/my_sensor_v1
```

### 2. Implement Your Sensor

Edit `device.py` and implement the `MySensor` class:

```python
from machine import Pin, ADC
from om_sensor import SensorInterface

class MySensor(SensorInterface):
    def __init__(self):
        self.adc = ADC(Pin(1))
        self.adc.atten(ADC.ATTN_11DB)

    def read(self):
        return {"values": [self.adc.read()]}
```

### 3. Configure Your Device

Edit `DEVICE_TYPE` and `DEVICE_DEFAULTS` in your device class:

```python
class MyDevice(BaseDevice):
    DEVICE_TYPE = "my_sensor"    # Unique identifier
    DEVICE_DEFAULTS = {
        "device_id": "my-sensor-01",
        "wifi_ssid": "OpenMuscle",
        "wifi_password": "3141592653",
        "udp_target_ip": "192.168.1.49",
        "udp_port": 3141,
        ...
    }
```

### 4. Flash to Device

```bash
# Upload shared library
mpremote cp embedded/lib/om_*.py :/lib/

# Upload device files
mpremote cp embedded/devices/my_sensor_v1/boot.py :/
mpremote cp embedded/devices/my_sensor_v1/device.py :/
mpremote mkdir :/config
mpremote cp embedded/devices/my_sensor_v1/config/defaults.json :/config/
```

### 5. Verify on PC

```bash
# Listen for packets
openmuscle receive --port 3141
```

Your device will appear automatically. The packet will have `"type": "my_sensor"`.

## File Structure

```
embedded/devices/my_sensor_v1/
    boot.py              # Entry point
    device.py            # MyDevice(BaseDevice) + MySensor(SensorInterface)
    config/
        defaults.json    # Default settings
```

## Shared Library Reference

| Module | Purpose |
|--------|---------|
| `om_device.py` | `BaseDevice` class with lifecycle |
| `om_sensor.py` | `SensorInterface` abstract base |
| `om_network.py` | WiFi + UDP + ESPNOW |
| `om_settings.py` | JSON config persistence |
| `om_display.py` | SSD1306 OLED wrapper |
| `om_packet.py` | Standard packet builder |
| `om_logger.py` | Debug logging |
| `om_menu.py` | Button menu system |

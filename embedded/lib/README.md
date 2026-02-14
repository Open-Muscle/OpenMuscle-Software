# OpenMuscle Shared Firmware Library

These modules are shared across all OpenMuscle embedded devices. They are uploaded to the ESP32's `/lib` directory so any device firmware can import them.

## Modules

| Module | Purpose |
|--------|---------|
| `om_device.py` | `BaseDevice` base class with standard lifecycle (init -> connect -> run) |
| `om_sensor.py` | `SensorInterface` abstract class for device-specific sensors |
| `om_network.py` | `NetworkManager` for WiFi, UDP, and ESPNOW communication |
| `om_settings.py` | `Settings` class for JSON config persistence |
| `om_display.py` | `Display` wrapper for SSD1306 OLED (safe no-op fallback) |
| `om_packet.py` | `build_packet()` for standard JSON packet construction |
| `om_logger.py` | Simple debug/info/warn/error logging |
| `om_menu.py` | `MenuManager` for button-driven UI with debouncing |

## Upload to Device

```bash
mpremote cp om_*.py :/lib/
```

## Usage

```python
from om_device import BaseDevice
from om_sensor import SensorInterface

class MySensor(SensorInterface):
    def read(self):
        return {"values": [1, 2, 3]}

class MyDevice(BaseDevice):
    DEVICE_TYPE = "my_device"
    DEVICE_DEFAULTS = {"wifi_ssid": "OpenMuscle", ...}

    def __init__(self):
        super().__init__()
        self.sensor = MySensor()

    async def run(self):
        # Your async logic here
        ...
```

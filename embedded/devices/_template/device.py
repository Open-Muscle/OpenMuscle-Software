# device.py - Device main application (TEMPLATE)
#
# Copy this template and customize for your new OpenMuscle device.
# See docs/adding-a-device.md for step-by-step instructions.

import uasyncio as asyncio
import om_logger as log
from om_device import BaseDevice
from om_sensor import SensorInterface

# --- Step 1: Implement your sensor ---

class MySensor(SensorInterface):
    def __init__(self):
        # Initialize your ADC pins, I2C sensors, etc.
        pass

    def read(self):
        # Return a dict matching your device's data schema.
        # This dict goes into the "data" field of the standard packet.
        # Example: {"values": [100, 200, 300, 400]}
        return {"values": []}

    def calibrate(self, settings):
        # Optional: implement calibration routine
        pass

# --- Step 2: Define your device ---

class MyDevice(BaseDevice):
    # Change these to match your device
    DEVICE_TYPE = "my_device"       # Used in packet "type" field
    DEVICE_DEFAULTS = {
        "device_id": "my-device-01",
        "wifi_ssid": "OpenMuscle",
        "wifi_password": "3141592653",
        "udp_target_ip": "192.168.1.49",
        "udp_port": 3141,
        "scl_pin": 9,
        "sda_pin": 8,
        "oled_width": 128,
        "oled_height": 64,
        "sample_interval_ms": 100,
    }

    def __init__(self):
        super().__init__()
        self.sensor = MySensor()

    async def run(self):
        # Step 3: Define your async tasks
        asyncio.create_task(self._sensor_loop())
        while True:
            await asyncio.sleep(1)

    async def _sensor_loop(self):
        interval = self.settings.get("sample_interval_ms", 100) / 1000
        while True:
            data = self.sensor.read()
            packet = self.make_packet(data)
            await self.network.send_udp(packet)
            await asyncio.sleep(interval)

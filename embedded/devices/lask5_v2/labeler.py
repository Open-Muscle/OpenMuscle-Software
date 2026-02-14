# labeler.py - LASK5 V2 main application
#
# 4-finger target value acquirer with joystick, ESPNOW + WiFi UDP.
# Uses the shared OpenMuscle library.

import time
from machine import Pin, ADC
import uasyncio as asyncio
import om_logger as log
from om_device import BaseDevice
from om_packet import build_packet
from sensor_pistons import SensorPistons
from display_lask5 import draw_taskbar

class LASK5(BaseDevice):
    DEVICE_TYPE = "lask5"
    DEVICE_DEFAULTS = {
        "device_id": "lask5-01",
        "wifi_ssid": "OpenMuscle",
        "wifi_password": "3141592653",
        "udp_target_ip": "192.168.1.48",
        "udp_port": 3145,
        "scl_pin": 9,
        "sda_pin": 8,
        "oled_width": 128,
        "oled_height": 32,
        "mins": [0, 0, 0, 0],
        "maxes": [2500, 2500, 2500, 2500],
        "led_pin": 15,
        "start_pin": 11,
        "select_pin": 10,
        "up_pin": 41,
        "down_pin": 42,
        "joystick_x_pin": 6,
        "joystick_y_pin": 5,
        "joystick_sw_pin": 7,
        "sample_rate_hz": 25,
    }

    def __init__(self):
        super().__init__()
        self.sensor = SensorPistons()

        # Buttons
        self.start_btn = Pin(self.settings.get("start_pin", 11), Pin.IN, Pin.PULL_UP)
        self.select_btn = Pin(self.settings.get("select_pin", 10), Pin.IN, Pin.PULL_UP)
        self.up_btn = Pin(self.settings.get("up_pin", 41), Pin.IN, Pin.PULL_UP)
        self.down_btn = Pin(self.settings.get("down_pin", 42), Pin.IN, Pin.PULL_UP)

        # Joystick
        jx_pin = self.settings.get("joystick_x_pin", 6)
        jy_pin = self.settings.get("joystick_y_pin", 5)
        self.joystick_x = ADC(Pin(jx_pin))
        self.joystick_x.atten(ADC.ATTN_11DB)
        self.joystick_y = ADC(Pin(jy_pin))
        self.joystick_y.atten(ADC.ATTN_11DB)

        # LED
        self.led = Pin(self.settings.get("led_pin", 15), Pin.OUT)

        # Calibration data
        self.mins = self.settings.get("mins", [0, 0, 0, 0])
        self.maxes = self.settings.get("maxes", [2500, 2500, 2500, 2500])

        # ESPNOW peer (broadcast by default)
        self.peer = b'\xff\xff\xff\xff\xff\xff'

    def blink(self, count=2, on_ms=300, off_ms=200):
        for _ in range(count):
            self.led.value(1)
            time.sleep_ms(on_ms)
            self.led.value(0)
            time.sleep_ms(off_ms)

    async def run(self):
        self.blink(2)

        # Initialize ESPNOW for P2P communication
        self.network.init_espnow()

        # Start the send loop
        asyncio.create_task(self._send_loop())
        asyncio.create_task(self._display_loop())

        while True:
            await asyncio.sleep(1)

    async def _send_loop(self):
        interval = 1.0 / self.settings.get("sample_rate_hz", 25)
        while True:
            data = self.sensor.read()
            data["joystick"] = {
                "x": self.joystick_x.read(),
                "y": self.joystick_y.read(),
            }
            packet = self.make_packet(data)
            await self.network.send_udp(packet)
            await asyncio.sleep(interval)

    async def _display_loop(self):
        while True:
            values = self.sensor.read_raw()
            draw_taskbar(
                self.display,
                values,
                self.mins,
                self.maxes,
                joystick_x=self.joystick_x.read(),
                joystick_y=self.joystick_y.read(),
            )
            await asyncio.sleep(0.1)

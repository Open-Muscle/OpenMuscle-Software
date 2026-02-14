# flexgrid.py - FlexGrid V1 main application
#
# 60-sensor (16x4) Velostat pressure sensor matrix on ESP32-S3.
# Uses the shared OpenMuscle library and spawns 3 async tasks:
#   1. sensor_loop - scan matrix, send UDP, update heatmap
#   2. display_loop - render menu state
#   3. menu_loop - poll buttons

import uasyncio as asyncio
import om_logger as log
from om_device import BaseDevice
from om_menu import MenuManager
from sensor_matrix import SensorMatrix
from display_flexgrid import draw_sensor_matrix, draw_menu

class FlexGrid(BaseDevice):
    DEVICE_TYPE = "flexgrid"
    DEVICE_DEFAULTS = {
        "device_id": "flexgrid-01",
        "wifi_ssid": "OpenMuscle",
        "wifi_password": "3141592653",
        "udp_target_ip": "192.168.1.49",
        "udp_port": 3141,
        "scl_pin": 9,
        "sda_pin": 8,
        "oled_width": 128,
        "oled_height": 64,
        "sample_interval_ms": 100,
        "select_pin": 10,
        "menu_pin": 21,
    }

    def __init__(self):
        super().__init__()
        self.sensor = SensorMatrix()
        self.menu = MenuManager(
            self.display,
            select_pin=self.settings.get("select_pin", 10),
            menu_pin=self.settings.get("menu_pin", 21),
        )
        self.menu.set_menus([
            ["Start Session", "Settings", "About"],
            ["Wi-Fi", "UDP Target", "Back"],
            ["Info", "Version", "Back"],
        ])

    async def run(self):
        log.info("Starting FlexGrid async loops")
        asyncio.create_task(self._sensor_loop())
        asyncio.create_task(self._display_loop())
        asyncio.create_task(self._menu_loop())
        while True:
            await asyncio.sleep(1)

    async def _sensor_loop(self):
        interval = self.settings.get("sample_interval_ms", 100) / 1000
        while True:
            matrix = self.sensor.scan_matrix()
            packet = self.make_packet({
                "matrix": matrix,
                "rows": self.sensor.num_rows,
                "cols": self.sensor.num_cols,
            })
            await self.network.send_udp(packet)
            draw_sensor_matrix(self.display, matrix)
            await asyncio.sleep(interval)

    async def _display_loop(self):
        while True:
            state = self.menu.get_state()
            draw_menu(self.display, state)
            await asyncio.sleep(0.25)

    async def _menu_loop(self):
        while True:
            self.menu.check_buttons()
            await asyncio.sleep(0.05)

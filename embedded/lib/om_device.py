# om_device.py - Base device class for all OpenMuscle devices
#
# Provides a standard lifecycle: init -> connect -> run
# Subclasses override DEVICE_TYPE, DEVICE_DEFAULTS, and run().

import uasyncio as asyncio
import om_logger as log
from om_settings import Settings
from om_network import NetworkManager
from om_display import Display
from om_packet import build_packet

class BaseDevice:
    """Standard device lifecycle for all OpenMuscle embedded devices."""

    DEVICE_TYPE = "unknown"
    DEVICE_DEFAULTS = {}

    def __init__(self):
        self.settings = Settings(self.DEVICE_DEFAULTS)
        self.network = NetworkManager(self.settings)
        self.display = Display(
            scl_pin=self.settings.get("scl_pin", 9),
            sda_pin=self.settings.get("sda_pin", 8),
            width=self.settings.get("oled_width", 128),
            height=self.settings.get("oled_height", 64),
            flip=self.settings.get("oled_flip", False),
        )
        self.device_id = self.settings.get("device_id", self.DEVICE_TYPE)

    async def start(self):
        """Boot sequence: show status, connect network, then run."""
        log.info("{} starting".format(self.DEVICE_TYPE))

        if self.display.available:
            self.display.fill(0)
            self.display.text(self.DEVICE_TYPE, 0, 0)
            self.display.text("Connecting...", 0, 16)
            self.display.show()

        try:
            await self.network.connect_wifi()
            log.info("Network connected: " + str(self.network.get_ip()))
            if self.display.available:
                self.display.fill(0)
                self.display.text("IP:", 0, 0)
                self.display.text(str(self.network.get_ip()), 0, 10)
                self.display.show()
        except Exception as e:
            log.error("Network error: {}".format(e))
            if self.display.available:
                self.display.fill(0)
                self.display.text("Net Error", 0, 0)
                self.display.text(str(e)[:16], 0, 10)
                self.display.show()

        await self.run()

    async def run(self):
        """Override in subclass. Typically spawns async tasks."""
        raise NotImplementedError

    def make_packet(self, data, metadata=None):
        """Build a standard packet with this device's type and id."""
        return build_packet(self.DEVICE_TYPE, self.device_id, data, metadata)

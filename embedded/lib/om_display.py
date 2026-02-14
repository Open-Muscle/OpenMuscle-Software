# om_display.py - SSD1306 OLED display wrapper for OpenMuscle devices
#
# Provides safe no-op fallback when hardware is not present.
# Device-specific rendering (heatmaps, bar charts) goes in device code,
# calling these primitives.

from machine import Pin, I2C
import om_logger as log

class Display:
    def __init__(self, scl_pin=9, sda_pin=8, width=128, height=64,
                 i2c_freq=400000, addr=0x3C):
        self.oled = None
        self.width = width
        self.height = height

        try:
            import ssd1306
            i2c = I2C(0, scl=Pin(scl_pin), sda=Pin(sda_pin), freq=i2c_freq)
            devices = i2c.scan()
            log.debug("I2C devices found: {}".format(devices))

            if addr not in devices:
                log.warn("SSD1306 not found at 0x{:02X}".format(addr))
                return

            self.oled = ssd1306.SSD1306_I2C(width, height, i2c, addr=addr)
            self.clear()
            log.info("SSD1306 initialized")
        except Exception as e:
            log.error("Display init failed: {}".format(e))

    @property
    def available(self):
        return self.oled is not None

    def clear(self):
        if self.oled:
            self.oled.fill(0)
            self.oled.show()

    def text(self, msg, x=0, y=0, color=1):
        if self.oled:
            self.oled.text(str(msg), x, y, color)

    def show(self):
        if self.oled:
            self.oled.show()

    def fill(self, color):
        if self.oled:
            self.oled.fill(color)

    def fill_rect(self, x, y, w, h, color):
        if self.oled:
            self.oled.fill_rect(x, y, w, h, color)

    def pixel(self, x, y, color):
        if self.oled:
            self.oled.pixel(x, y, color)

    def rect(self, x, y, w, h, color):
        if self.oled:
            self.oled.rect(x, y, w, h, color)

# om_display.py - SSD1306 OLED display wrapper for OpenMuscle devices
#
# Provides safe no-op fallback when hardware is not present.
# Device-specific rendering (heatmaps, bar charts) goes in device code,
# calling these primitives.

from machine import Pin, I2C
import om_logger as log

class Display:
    def __init__(self, scl_pin=9, sda_pin=8, width=128, height=64,
                 i2c_freq=400000, addr=0x3C, flip=False):
        """
        Args:
            flip: If True, rotate the display 180 degrees. This is a hardware
                  mounting concern -- whether the OLED is right-side-up on
                  the PCB. Set via per-unit settings.json `oled_flip` key
                  (the default driver init is correct for FlexGrid V3 boards
                  but inverted for the LASK5 v2 enclosure).
        """
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
            if flip:
                self._apply_flip()
            self.clear()
            log.info("SSD1306 initialized" + (" (flipped)" if flip else ""))
        except Exception as e:
            log.error("Display init failed: {}".format(e))

    def _apply_flip(self):
        """Rotate the panel 180 degrees via SSD1306 hardware commands.

        Tries the driver's high-level `rotate()` first (newer ssd1306 module),
        falls back to direct register writes (SEG_REMAP + COM_OUT_DIR) for
        older driver builds where rotate() is missing. Either way fails open
        -- if neither works we just keep the default orientation.
        """
        if self.oled is None:
            return
        try:
            self.oled.rotate(False)   # False = unflipped-from-driver-default = 180 from physical
            return
        except Exception:
            pass
        try:
            # SET_SEG_REMAP | 0x00 (default driver uses 0x01 = 0xA1)
            self.oled.write_cmd(0xA0)
            # SET_COM_OUT_DIR | 0x00 (default driver uses 0x08 = 0xC8)
            self.oled.write_cmd(0xC0)
        except Exception as e:
            log.warn("OLED flip failed: {}".format(e))

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

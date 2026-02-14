# sensor_pistons.py - LASK5 4-channel piston/hall sensor interface
#
# Reads 4 ADC channels (finger pressure sensors), supports calibration
# with min/max normalization and multi-sample averaging.

from machine import Pin, ADC
import time
from om_sensor import SensorInterface

class SensorPistons(SensorInterface):
    def __init__(self, adc_pins=(1, 2, 3, 4), attenuation=ADC.ATTN_11DB,
                 buffer_size=10):
        self.sensors = []
        for pin_num in adc_pins:
            adc = ADC(Pin(pin_num))
            adc.atten(attenuation)
            self.sensors.append(adc)

        self.num_channels = len(self.sensors)
        self.buffer_size = buffer_size
        self._buffers = [[] for _ in range(self.num_channels)]

    def read_raw(self):
        """Read raw ADC values from all channels."""
        return [s.read() for s in self.sensors]

    def read_averaged(self):
        """Read with multi-sample averaging for noise reduction."""
        for i in range(self.num_channels):
            reading = self.sensors[i].read()
            self._buffers[i].append(reading)
            if len(self._buffers[i]) > self.buffer_size:
                self._buffers[i].pop(0)

        return [
            sum(buf) // len(buf) if buf else 0
            for buf in self._buffers
        ]

    def read(self):
        """SensorInterface implementation. Returns averaged values."""
        values = self.read_averaged()
        return {"values": values}

    def calibrate(self, settings):
        """
        Two-step calibration:
        1. Read max values (pistons released)
        2. Read min values (pistons pressed)
        Stores mins/maxes in settings.
        """
        import om_logger as log

        log.info("Calibration: release all pistons...")
        time.sleep(2)
        maxes = self.read_raw()
        log.info("Maxes recorded: {}".format(maxes))

        log.info("Calibration: press all pistons, then wait...")
        time.sleep(3)
        mins = self.read_raw()
        log.info("Mins recorded: {}".format(mins))

        settings["mins"] = mins
        settings["maxes"] = maxes
        settings.save()
        log.info("Calibration saved")

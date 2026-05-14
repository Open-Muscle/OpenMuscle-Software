# sensor_pistons.py - LASK5 4-channel piston/hall sensor interface
#
# Reads 4 ADC channels (finger pressure sensors), supports calibration
# with min/max normalization and multi-sample averaging.
#
# Wire format (`read()` -> packet data["values"]) is calibrated 0..1 floats,
# matching the monolithic firmware's calibrated output. Use `read_raw()` when
# you need raw averaged ADCs (e.g. for the on-OLED bar chart or for the
# calibration routine which deliberately reads pre-calibration values).

from machine import Pin, ADC
import time
from om_sensor import SensorInterface

class SensorPistons(SensorInterface):
    def __init__(self, adc_pins=(1, 2, 3, 4), attenuation=ADC.ATTN_11DB,
                 buffer_size=10, mins=None, maxes=None):
        self.sensors = []
        for pin_num in adc_pins:
            adc = ADC(Pin(pin_num))
            adc.atten(attenuation)
            self.sensors.append(adc)

        self.num_channels = len(self.sensors)
        self.buffer_size = buffer_size
        self._buffers = [[] for _ in range(self.num_channels)]

        # Calibration: pistons released -> max ADC, pressed -> min ADC.
        # Defaults give a no-op identity-ish mapping until real calibration runs.
        self.mins = list(mins) if mins else [0] * self.num_channels
        self.maxes = list(maxes) if maxes else [4095] * self.num_channels

    def set_calibration(self, mins, maxes):
        """Update calibration in place (used after Calibrate menu runs)."""
        self.mins = list(mins)
        self.maxes = list(maxes)

    def read_raw(self):
        """Read raw ADC values from all channels (no averaging, no calibration)."""
        return [s.read() for s in self.sensors]

    def read_averaged(self):
        """Read with multi-sample averaging for noise reduction. Returns raw ints."""
        for i in range(self.num_channels):
            reading = self.sensors[i].read()
            self._buffers[i].append(reading)
            if len(self._buffers[i]) > self.buffer_size:
                self._buffers[i].pop(0)

        return [
            sum(buf) // len(buf) if buf else 0
            for buf in self._buffers
        ]

    def read_calibrated(self):
        """Return 4 calibrated 0..1 floats applied to the averaged ADC values.

        Inverts the mapping so that "fully pressed" -> 1.0 and "fully released"
        -> 0.0 (matches monolithic firmware convention).
        """
        averaged = self.read_averaged()
        out = []
        for i, val in enumerate(averaged):
            span = self.maxes[i] - self.mins[i]
            if span == 0:
                span = 1  # avoid divide-by-zero
            norm = (val - self.mins[i]) / span
            # Clamp to [0, 1]
            if norm < 0.0:
                norm = 0.0
            elif norm > 1.0:
                norm = 1.0
            out.append(norm)
        return out

    def read(self):
        """SensorInterface implementation. Returns calibrated 0..1 floats.

        This is what flows into the UDP packet's data["values"] -- matches the
        monolithic firmware's wire format and what the openmuscle web UI
        expects for the LASK5 piston bars.
        """
        return {"values": self.read_calibrated()}

    def record_maxes(self):
        """Sample raw ADCs as the new 'maxes' (pistons released). Returns the
        sampled list. Does NOT persist; caller handles save."""
        self.maxes = self.read_raw()
        return self.maxes

    def record_mins(self):
        """Sample raw ADCs as the new 'mins' (pistons pressed). Returns the
        sampled list. Does NOT persist; caller handles save."""
        self.mins = self.read_raw()
        return self.mins

    def save_calibration(self, settings):
        """Persist current mins/maxes to the given Settings instance."""
        settings["mins"] = self.mins
        settings["maxes"] = self.maxes
        settings.save()

    def calibrate(self, settings, display=None, release_delay_s=2, press_delay_s=3):
        """Synchronous two-step calibration (blocks for ~5s).

        Kept for non-async callers / one-shot setup scripts. Async callers
        (e.g. labeler menu) should use record_maxes/record_mins/save_calibration
        directly with `await uasyncio.sleep(...)` so the event loop isn't
        starved during the wait periods.
        """
        import om_logger as log

        def _prompt(line1, line2=""):
            if display is not None and display.available:
                display.fill(0)
                display.text(line1, 0, 0)
                if line2:
                    display.text(line2, 0, 12)
                display.show()
            log.info(line1 + (" " + line2 if line2 else ""))

        _prompt("Calibrate:", "release pistons")
        time.sleep(release_delay_s)
        maxes = self.record_maxes()
        _prompt("Maxes:", str(maxes))
        log.info("Maxes recorded: {}".format(maxes))

        _prompt("Calibrate:", "press pistons")
        time.sleep(press_delay_s)
        mins = self.record_mins()
        _prompt("Mins:", str(mins))
        log.info("Mins recorded: {}".format(mins))

        self.save_calibration(settings)
        log.info("Calibration saved")
        _prompt("Calibration", "saved")
        time.sleep(1)

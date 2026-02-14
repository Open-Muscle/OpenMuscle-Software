# sensor_matrix.py - FlexGrid 16x4 MUX sensor scanning
#
# Device-specific sensor implementation for the FlexGrid V1.
# Scans a 16-column x 4-row pressure sensor matrix via 4-bit MUX addressing.

from machine import Pin, ADC
import time
from om_sensor import SensorInterface

class SensorMatrix(SensorInterface):
    def __init__(self, adc_pins=(1, 2, 3, 4), mux_pins=(5, 6, 7, 15),
                 mux_en_pin=16, attenuation=ADC.ATTN_11DB, delay_us=100):
        # MUX address pins (4-bit binary)
        self.S0 = Pin(mux_pins[0], Pin.OUT)
        self.S1 = Pin(mux_pins[1], Pin.OUT)
        self.S2 = Pin(mux_pins[2], Pin.OUT)
        self.S3 = Pin(mux_pins[3], Pin.OUT)
        self.mux_en = Pin(mux_en_pin, Pin.OUT)
        self.mux_en.value(0)  # LOW = enable

        # ADC channels for each row
        self.adc = []
        for pin_num in adc_pins:
            adc = ADC(Pin(pin_num))
            adc.atten(attenuation)
            self.adc.append(adc)

        self.num_cols = 16
        self.num_rows = len(self.adc)
        self.delay_us = delay_us

    def _select_channel(self, channel):
        bits = [(channel >> i) & 1 for i in range(4)]
        self.S0.value(bits[0])
        self.S1.value(bits[1])
        self.S2.value(bits[2])
        self.S3.value(bits[3])

    def scan_matrix(self):
        """Scan all 16 columns x N rows. Returns list of lists."""
        matrix = [[0] * self.num_rows for _ in range(self.num_cols)]
        for col in range(self.num_cols):
            self._select_channel(col)
            time.sleep_us(self.delay_us)
            for row in range(self.num_rows):
                matrix[col][row] = self.adc[row].read()
        return matrix

    def read(self):
        """SensorInterface implementation."""
        matrix = self.scan_matrix()
        return {
            "matrix": matrix,
            "rows": self.num_rows,
            "cols": self.num_cols,
        }

    def calibrate(self, settings):
        pass

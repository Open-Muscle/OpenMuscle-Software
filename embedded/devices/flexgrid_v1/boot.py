# boot.py - FlexGrid V1 boot entry point
#
# Hardware: Open Muscle FlexGrid - 60 Sensor 15x4 Velostat pressure matrix
# Platform: ESP32-S3 running MicroPython
#
# Upload to device along with:
#   - This file (boot.py)
#   - flexgrid.py, sensor_matrix.py, display_flexgrid.py
#   - Shared lib: embedded/lib/om_*.py -> device /lib/

import uasyncio as asyncio
from flexgrid import FlexGrid

device = FlexGrid()
asyncio.run(device.start())

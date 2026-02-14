# boot.py - LASK5 V2 boot entry point
#
# Hardware: Open Muscle Labeler (LASK5) - 4 Finger Target Value Acquirer
# Platform: ESP32-S3 running MicroPython
#
# Upload to device along with:
#   - This file (boot.py)
#   - labeler.py, sensor_pistons.py, display_lask5.py
#   - Shared lib: embedded/lib/om_*.py -> device /lib/

import uasyncio as asyncio
from labeler import LASK5

device = LASK5()
asyncio.run(device.start())

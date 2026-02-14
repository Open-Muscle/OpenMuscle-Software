# boot.py - Device boot entry point (TEMPLATE)
#
# Replace MyDevice with your device class name.
# Upload to device along with:
#   - This file (boot.py)
#   - device.py, your sensor module, your display module
#   - Shared lib: embedded/lib/om_*.py -> device /lib/

import uasyncio as asyncio
from device import MyDevice

device = MyDevice()
asyncio.run(device.start())

# Archive

This directory contains legacy and deprecated code preserved for reference. **Do not use these files for new development.**

## Contents

- `embedded/FlexGrid_V0/` - FlexGrid V0 firmware (monolithic, ST7735 TFT, blocking I/O). Replaced by FlexGrid V1.
- `embedded/LASK5_V1/` - LASK5 V1 labeler firmware. Replaced by LASK5 V2.
- `embedded/SensorBand/` - Early OM12 MVP prototype. Abandoned.
- `embedded/wifi_test_boot.py`, `wifi_test_boot_better.py` - WiFi connectivity test scripts.
- `embedded/image_ssd1306_convert.py` - Image conversion utility for OLED.
- `pc/Old/` - Deprecated PC scripts for data capture and training.
- `pc/socket_test.py`, `pc/test_receive.py` - Network debugging scripts.

## Why archived?

These files were used during prototyping and testing. The active codebase has moved to:
- `embedded/lib/` - Shared firmware library
- `embedded/devices/` - Production device firmware
- `pc/src/openmuscle/` - Unified PC Python package

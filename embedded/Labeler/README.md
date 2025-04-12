
# OpenMuscle Labeler (MicroPython Firmware)

> **Part of the** [**OpenMuscle**](https://openmuscle.org) **open‑hardware prosthetics ecosystem**  
> This repository contains the MicroPython firmware that turns an ESP32‑based dev‑kit into a data‑labeling companion for the OpenMuscle forearm band.  
> It shows live sensor values on an SSD1306 OLED and broadcasts them over ESP‑NOW or via UDP (WiFi Access Point Needed) so that the desktop _OpenMuscle‑Trainer_ can associate the raw pressure vectors with finger‑pose labels.

## ✨ Features

-   Asynchronous, non‑blocking architecture built on `uasyncio`
    
-   High‑speed, low‑latency wireless streaming via **ESP‑NOW**
    
-   128 × 64 OLED visualization (SSD1306 I²C)
    
-   Auto‑discoverable by the OpenMuscle Trainer
    
-   Runs on any ESP32‑S3 / ESP32‑C3 board with ≥ 2 MB flash
    

## 🛠️ Hardware

ESP32‑S3 dev‑kit
0.96″ SSD1306 OLED
Li‑Po battery (optional)
3.7 V with on‑board charger

Wire **SDA → GPIO 6** and **SCL → GPIO 7** by default, or adjust in `config.py`.

## ⚡ Quick‑start

### 1. Flash MicroPython

Download the latest **ESP32‑S3** build from the official site and flash:

```
esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 460800 erase_flash
esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 460800 write_flash -z 0x0 firmware.bin
```

### 2. Install `mpremote`

```
pip install --upgrade mpremote
```

### 3. Pull the source tree

```
git clone https://github.com/Open-Muscle/OpenMuscle-Software.git
cd OpenMuscle-Software/embedded/Labeler
```

### 4. Push the code and **install the MicroPython packages**

> **Why** `**mip**`**?** MicroPython 1.20+ ships a tiny package manager called **mip** that can fetch wheels directly from PyPI and cache them in flash.

```
# Copy project files
mpremote connect /dev/ttyUSB0 fs cp -r src :app

# Enter the REPL once to create /lib if it does not exist
mpremote connect /dev/ttyUSB0 repl
>>> import os, sys; os.mkdir("/lib")
>>> sys.exit()

# Install run‑time dependencies
mpremote mip install asyncio          # uasyncio back‑port from micropython‑lib
mpremote mip install micropython-ssd1306
mpremote mip install aioespnow        # async wrapper for ESP‑NOW
```

> **Tip:** If you prefer frozen modules, add the three libs to your board description and rebuild the firmware.

### 5. Reboot & run

```
mpremote connect /dev/ttyUSB0 run main.py
```

The OLED should light up with the OpenMuscle logo and begin streaming packets.

## 📂 Repository layout

```
embedded/Labeler
├── src/
│   ├── main.py          # entry point
│   ├── config.py        # pin map & credentials
│   ├── display.py       # SSD1306 UI helpers
│   └── radio.py         # ESP‑NOW TX wrapper
└── README.md            # ← you are here
```

## 🤝 Contributing

Pull‑requests, bug reports and feature ideas are welcome! Please file an issue first so we can discuss the scope.

1.  Fork → feature branch → PR
    
2.  Ensure `pre-commit` passes (`black`, `ruff`, `mdformat`)
    
3.  Describe _why_ the change is needed
    

## 📄 License

This firmware is released under the **MIT License**—see `LICENSE` for details.

'''
# LASK5 V2 – Embedded Firmware

This folder contains the MicroPython firmware for the **LASK5 V2** device, an updated version of the original OpenMuscle labeler. V2 introduces hardware layout improvements, a more robust button interface, and better ESP32 integration for reliable operation and asynchronous data transmission.

---

## 🆕 What's New in Version 2

- **Transition to ESP32 VROOM-1 (R16N8/N16R8)** module from dev board
- **New power circuit**: includes dedicated power-on, boot, and reset buttons
- **Expanded button layout**: 5 total buttons, including a menu navigation interface
- **Proper headers**: reliable I/O and component access for labeling tasks
- **Support for BMI160 IMU** for motion sensing
- **Asynchronous data handling** using AIO ESPNOW
- **OLED display support** with SSD1306

---

## ⚙️ Required Libraries

Install dependencies using `mpremote mip` after flashing MicroPython:

```bash
mpremote mip install github:jposada202020/MicroPython_BMI160
mpremote mip install ssd1306
mpremote mip install aioespnow
```

---

## 🚀 Installation

1. **Flash MicroPython Firmware**

Download [esptool.py](https://github.com/espressif/esptool) and run:

```bash
esptool.py --chip esp32 --port /dev/ttyUSB0 erase_flash
esptool.py --chip esp32 --port /dev/ttyUSB0 write_flash -z 0x1000 esp32-xxxxxx.bin
```

2. **Install Libraries**

After reboot, install libraries with:

```bash
mpremote mip install ...
```

3. **Upload Core Files**

Transfer:
- `boot.py`
- `networkmanager.py`
- `main.py` (or whichever script launches the UI)
- `loadscreen.py` (animation)

---

## 🧾 Pinouts

| Function               | GPIO |
|------------------------|------|
| I2C SCL (IMU, OLED)    | 9    |
| I2C SDA                | 8    |
| Button - Menu Up       | 11   |
| Button - Menu Down     | 10   |
| Joystick Push Button   | 7    |
| Joystick X-Axis (ADC)  | 6    |
| Joystick Y-Axis (ADC)  | 5    |
| Power Button           | TBD  |
| Reset / Boot           | TBD  |

> *Note: Fill in `TBD` once you confirm the GPIO assignments for boot and power.*

---

## 🛠 Usage

This firmware controls the labeling interface on the LASK5 hardware. After boot, the device initializes an OLED display, IMU tracking, and awaits user input via the joystick and buttons. Data is labeled and transmitted asynchronously using ESPNOW.

---

## 📜 License & Contribution

Licensed under CERN-OHL-S-2.0. Contributions welcome!

A formal contributor guide will be added to the main repo soon.

---
'''

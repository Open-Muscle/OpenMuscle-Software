# OpenMuscle FlexGrid Boot
# Flex sensor matrix visualizer with menu
# TURFPTAx – v1.1 – 06-05-2025

import time, gc
from machine import Pin, ADC, I2C, SPI
import ssd1306
import image_loader  # Optional loading screen

# === Hardware Configuration ===
defaults = {
    'oledWIDTH': 128,
    'oledHEIGHT': 64,
    'sclPIN': 9,
    'sdaPIN': 8,
    'selectPIN': 10,
    'menuPIN': 21,
    'battery_pin': 12,
    'mux_en': 16,
    'S0': 5,
    'S1': 6,
    'S2': 7,
    'S3': 15,
    'adc_pins': [1, 2, 3, 4]
}

# === Load Pin Config ===
sclPIN = defaults['sclPIN']
sdaPIN = defaults['sdaPIN']
oledWIDTH = defaults['oledWIDTH']
oledHEIGHT = defaults['oledHEIGHT']
battery_pin = defaults['battery_pin']

# === MUX Select ===
S = [
    Pin(defaults['S0'], Pin.OUT),
    Pin(defaults['S1'], Pin.OUT),
    Pin(defaults['S2'], Pin.OUT),
    Pin(defaults['S3'], Pin.OUT)
]
mux_en = Pin(defaults['mux_en'], Pin.OUT)
mux_en.value(0)  # enable MUX

# === ADCs ===
adc_pins = defaults['adc_pins']
adc = [ADC(Pin(p)) for p in adc_pins]
for a in adc:
    a.atten(ADC.ATTN_11DB)

# === Input Buttons ===
select_button = Pin(defaults['selectPIN'], Pin.IN, Pin.PULL_UP)
menu_button = Pin(defaults['menuPIN'], Pin.IN, Pin.PULL_UP)

# === OLED Setup ===
def init_oled():
    try:
        i2c = I2C(scl=Pin(sclPIN), sda=Pin(sdaPIN))
        print(oledWIDTH, oledHEIGHT, i2c)
        oled = ssd1306.SSD1306_I2C(128, 64, i2c)
        print("[OLED] Ready")
        oled.rotate(False)
        return oled
    except Exception as e:
        print("[OLED] Failed:", e)
        return None

oled = init_oled()
if oled:
    image_loader.loading_screen(oled)  # Optional logo

# === UI Feedback ===
def frint(text, oled=oled, ram=[]):
    if oled:
        text = str(text)
        ram.append(text if len(text) <= 16 else text[0:5] + '..' + text[-9:])
        oled.fill(0)
        for i, msg in enumerate(ram[-4:]):
            oled.text(msg, 0, i * 8)
        oled.show()
    print(text)

# === MUX Channel Selector ===
def select_mux_channel(channel):
    bits = [(channel >> i) & 1 for i in range(4)]
    for i, s in enumerate(S):
        s.value(bits[i])

# === Sensor Display ===
def draw_column_and_bars(oled, column_num, values):
    oled.fill(0)
    oled.text(f"Col: {column_num}", 0, 0)
    max_val = 4095
    bar_width = 100
    bar_height = 8
    start_y = 10
    for i, val in enumerate(values):
        scaled = min(int((val / max_val) * bar_width), bar_width)
        y = start_y + i * (bar_height + 2)
        oled.rect(0, y, bar_width, bar_height, 1)
        oled.fill_rect(0, y, scaled, bar_height, 1)
    oled.show()

# === Menu System ===
def flexgrid_menu():
    menu_items = [
        "View Live Matrix",
        "Exit"
    ]
    current = 0
    while True:
        oled.fill(0)
        for i, item in enumerate(menu_items):
            y = i * 10
            if i == current:
                oled.fill_rect(0, y, oledWIDTH, 10, 1)
                oled.text(item, 2, y, 0)
            else:
                oled.text(item, 2, y, 1)
        oled.show()

        if select_button.value() == 0:
            wait_for_button_release(select_button)
            if current == 0:
                run_column_viewer()
            elif current == 1:
                oled.fill(0)
                oled.text("Exiting...", 0, 0)
                oled.show()
                break

        if menu_button.value() == 0:
            wait_for_button_release(menu_button)
            current = (current + 1) % len(menu_items)

        time.sleep(0.1)

# === Column Viewer Mode ===
def run_column_viewer():
    col = 0
    while True:
        if select_button.value() == 0:
            col = (col + 1) % 16
            wait_for_button_release(select_button)

        if menu_button.value() == 0:
            wait_for_button_release(menu_button)
            break  # Return to menu

        select_mux_channel(col)
        time.sleep_us(200)
        values = [adc[i].read() for i in range(4)]
        draw_column_and_bars(oled, col, values)
        time.sleep(0.1)

# === Debounce ===
def wait_for_button_release(pin):
    while pin.value() == 0:
        time.sleep_ms(10)
    time.sleep_ms(50)

# === Main Loop ===
def main():
    frint("FlexGrid Booting...")
    time.sleep(1)
    flexgrid_menu()

if __name__ == "__main__":
    main()

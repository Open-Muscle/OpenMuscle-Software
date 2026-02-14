# Hardware Open Muscle FlexGrid - (FlexGrid) V1
# 60 Sensor 15x4 Velostat pressure sensor matrix
# Software version 0.1.0
# Coded for ESP32-S3
# 06-02-2025 - TURFPTAx

import ssd1306
import time
import math

from machine import Pin, ADC, I2C, SPI
import time

import image_loader

# Globals
count = 0
zero = [0,0,0,0]
hall = []
ram = []
s,wlan = False,False
peer = b'\xff' * 6   # MAC address of peer's wifi interface

# Battery sensing and I2C pins
battery_voltage_pin = 12
sclPIN = 9
sdaPIN = 8

scl = Pin(sclPIN)
sda = Pin(sdaPIN)
oledWIDTH = 128
oledHEIGHT =  64

#i2c = I2C(scl=scl, sda=sda)
#print("I2C Devices Found:", i2c.scan())

# MUX selector pins
S0 = Pin(5, Pin.OUT)
S1 = Pin(6, Pin.OUT)
S2 = Pin(7, Pin.OUT)
S3 = Pin(15, Pin.OUT)
mux_en = Pin(16, Pin.OUT)  # GP16_E is connected here
mux_en.value(0)  # Set LOW to enable MUX

# ADC channels for ROW_0 to ROW_3 (GPIO1-4)
adc_pins = [1, 2, 3, 4]
adc = [ADC(Pin(pin)) for pin in adc_pins]

# Set attenuation to read full 0–3.3V range
for a in adc:
    a.atten(ADC.ATTN_11DB)
    
select_button = Pin(10, Pin.IN, Pin.PULL_UP)  # Active LOW
menu_button = Pin(21, Pin.IN, Pin.PULL_UP)  # Active LOW

# SPI setup (match your pinout)
spi = SPI(2, baudrate=20000000, polarity=0, phase=0,
          sck=Pin(12), mosi=Pin(11), miso=Pin(13))

def wait_for_button_press(pin):
    """Waits for a button press and release (debounced)."""
    while pin.value() == 0:  # wait until released
        time.sleep_ms(10)
    time.sleep_ms(50)  # debounce delay

def initOLED(scl=scl,sda=sda,w=oledWIDTH,h=oledHEIGHT):
    print('scl = ',scl)
    print('sda = ',sda)
    oled = False
    i2c = False
    time.sleep(1)
    try:
        i2c = I2C(scl=scl,sda=sda)
        print(i2c)
    except:
        print('i2c failed check pins scl sda')
        try:
            print('i2c.scan() = ',i2c.scan())
        except Exception as e:
            print(f'i2c.scan() failed e:{e}')
    if i2c:
        try:
            oled = ssd1306.SSD1306_I2C(w,h,i2c)
            print("SSD1306 initialized[Y]")  
            print('oled = ',oled)
            oled.rotate(False)
        except:
            print("failed to initialize onboard SSD1306")
    return i2c,oled

i2c, oled = initOLED()
if oled:
    image_loader.loading_screen(oled)

def frint(text,oled=oled,ram=ram):
    if oled:
        if text:
            text = str(text)
            if len(text) <= 16:
                ram.append(text)
            else:
                ram.append(text[0:5]+'..'+text[len(text)-9:])
        oled.fill(0)
        n = 0
        for i in ram[-4:]:
            oled.text(i,0,n*8)
            n+=1
        if len(ram) > 9:
            ram = ram[-9:]
        gc.collect()
        oled.show()
        print('f:> ',ram[-1])
    else:
        print('f:< ',text)
        
frint('works')

def draw_column_and_bars(oled, column_num, values):
    """
    Draw column number and 4 bar graphs for the given values.
    OLED: SSD1306 instance
    column_num: int, column being displayed
    values: list of 4 ADC values (0–4095)
    """
    oled.fill(0)  # Clear screen

    # Show column number at the top
    oled.text(f"Col: {column_num} FlexGrid V0", 0, 0)

    max_val = 4095  # ADC range for scaling
    bar_width = 100
    bar_height = 8
    start_y = 12

    for i, val in enumerate(values):
        # Scale value to bar width (100px)
        scaled = min(int((val / max_val) * bar_width), bar_width)
        y = start_y + i * (bar_height + 2)
        oled.rect(0, y, bar_width, bar_height, 1)          # draw bar outline
        oled.fill_rect(0, y, scaled, bar_height, 1)        # fill actual value

    oled.show()
    
def select_mux_channel(channel):
    """Selects a channel (0–15) on the MUX."""
    bits = [(channel >> i) & 1 for i in range(4)]
    S0.value(bits[0])
    S1.value(bits[1])
    S2.value(bits[2])
    S3.value(bits[3])

def scan_matrix(delay_us=100):
    """Return a 16x4 matrix of sensor values."""
    matrix = [[0]*4 for _ in range(16)]  # 16 cols x 4 rows

    for col in range(16):
        select_mux_channel(col)
        time.sleep_us(delay_us)  # settle time
        for row in range(4):
            matrix[col][row] = adc[row].read()
    return matrix

def print_matrix(matrix):
    """Prints the matrix as rows (1–4) across columns (0–15)."""
    print("\n--- SENSOR MATRIX ---")
    print("     " + " ".join([f"C{c:02}" for c in range(16)]))
    for row in range(4):
        row_values = [f"{matrix[col][row]:4}" for col in range(16)]
        print(f"R{row+1}: " + " ".join(row_values))
    print("---------------------\n")
    
def draw_sensor_matrix(oled, matrix):
    """Draw a 15x4 matrix of 7x7 blocks representing pressure levels."""
    oled.fill(0)
    
    max_val = 4095
    cell_size = 7
    x_offset = (oledWIDTH - (15 * cell_size)) // 2  # Center horizontally
    y_offset = (oledHEIGHT - (4 * cell_size)) // 2  # Center vertically

    for col in range(15):
        for row in range(4):
            val = matrix[col][row]
            x = x_offset + col * cell_size
            y = y_offset + row * cell_size

            # Thresholds for visual levels
            if val < 200:  # No pressure
                continue
            elif val < 1000:
                oled.pixel(x + 3, y + 3, 1)
            elif val < 2000:
                oled.fill_rect(x + 2, y + 2, 3, 3, 1)
            elif val < 3000:
                oled.fill_rect(x + 1, y + 1, 5, 5, 1)
            else:
                oled.fill_rect(x, y, 7, 7, 1)

    oled.show()
    
def scan(i2c=i2c):
    for i in range(1000):
        i2c.scan()

if __name__ == "__main__":
    col = 0
    view_mode = 0  # 0 = column bars, 1 = matrix view

    while True:
        if select_button.value() == 0:  # Toggle view
            view_mode = (view_mode + 1) % 2
            wait_for_button_press(select_button)

        if menu_button.value() == 0:  # Exit
            wait_for_button_press(menu_button)
            oled.fill(0)
            oled.text("Exiting...", 0, 0)
            oled.show()
            break

        if view_mode == 0:
            select_mux_channel(col)
            time.sleep_us(200)
            values = [adc[i].read() for i in range(4)]
            draw_column_and_bars(oled, col, values)
            col = (col + 1) % 16
        else:
            matrix = scan_matrix()
            draw_sensor_matrix(oled, matrix)

        time.sleep(0.15)
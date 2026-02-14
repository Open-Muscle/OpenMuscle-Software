# Hardware Open Muscle Labler - (LASK5) V1
# 4 Finger Target Value Acquirer + Joystick?
# Software version 0.1.3
# Coded for ESP32-S3
# 11-29-2024 -TURFPTAx

import machine
import time

from network_manager import NetworkManager
#import network
#import espnow
#import socket

import ssd1306
import gc
import json

# Globals
count = 0
zero = [0,0,0,0]
hall = []
ram = []
s,wlan = False,False
peer = b'\xff' * 6   # MAC address of peer's wifi interface

from settings_manager import SettingsManager
#from network_manager import NetworkManager

# Define default settings
defaults = {
    'device_name': 'OpenMuscle Labeler',
    'sensor_mapping': False,
    'device_mac': False,
    'ledPIN': 8,
    'SSID':'OpenMuscle',
    'Pass':'3141592653',
    'PCIP': '192.168.1.48',
    'mins': [0, 0, 0, 0],
    'maxes': [2500, 2500, 2500, 2500],
    'led' : False,
    'ledPIN' : 15,
    'sclPIN' : 9,
    'sdaPIN' : 8,
    'oledWIDTH' : 128,
    'oledHEIGHT' :  32,
    'startPIN' : 11,
    'selectPIN' : 10,
    'upPIN' : 12,
    'downPIN' : 13,
    'joystick_xPIN' : 6,
    'joystick_yPIN' : 5,
    'Joystick_SW' : 7
    # Add other default settings
}


# Initialize SettingsManager
config = SettingsManager(defaults=defaults)

if config.is_first_run:
    # First run setup
    print("First run detected. Performing initial setup...")
    # Save initial settings
    config.save()
else:
    print("Loaded saved settings.")

# Load calibration data at startup
mins = config['mins']
maxes = config['maxes']
led = config['led']
ledPIN = config['ledPIN']
sclPIN = config['sclPIN']
sdaPIN = config['sdaPIN']
oledWIDTH = config['oledWIDTH']
oledHEIGHT =  config['oledHEIGHT']
startPIN = config['startPIN']
selectPIN = config['selectPIN']
upPIN = config['upPIN']
downPIN = config['downPIN']
joystick_xPIN = config['joystick_xPIN']
joystick_yPIN = config['joystick_yPIN']
Joystick_SW = config['Joystick_SW']
PCIP = config['CPIP']
SSID = config['SSID']
Pass = config['Pass']
# Example save to settings fiel
# config['mins'] = mins
# config.save()

#Button variables
start = machine.Pin(startPIN,machine.Pin.IN,machine.Pin.PULL_UP)
select = machine.Pin(selectPIN,machine.Pin.IN,machine.Pin.PULL_UP)
up = machine.Pin(upPIN,machine.Pin.IN,machine.Pin.PULL_UP)
down = machine.Pin(downPIN,machine.Pin.IN,machine.Pin.PULL_UP)
# Initialize joystick pins as ADC
joystick_x = machine.ADC(machine.Pin(joystick_xPIN))
joystick_x.atten(machine.ADC.ATTN_11DB)
joystick_y = machine.ADC(machine.Pin(joystick_yPIN))
joystick_y.atten(machine.ADC.ATTN_11DB)

# Optional joystick switch button
joystick_sw = machine.Pin(Joystick_SW, machine.Pin.IN, machine.Pin.PULL_UP)

#Startup Sequence
led = machine.Pin(15,machine.Pin.OUT)

# Find voltage Pin
# Need to find the schematic for the S3 Dev Board internal votage pin
battery_voltage_pin = 20
batt_level = machine.ADC(machine.Pin(battery_voltage_pin))

def blink(x):
    for _ in range(x):
      led.value(1)
      time.sleep(.3)
      led.value(0)
      time.sleep(.2)

blink(7)

def initOLED(scl=machine.Pin(sclPIN),sda=machine.Pin(sdaPIN),led=led,w=oledWIDTH,h=oledHEIGHT):
    print('scl = ',scl)
    print('sda = ',sda)
    oled = False
    i2c = False
    try:
        i2c = machine.I2C(scl=scl,sda=sda)
    except:
        print('i2c failed check pins scl sda')
        try:
            print('i2c.scan() = ',i2c.scan())
        except:
            print('i2c.scan() failed')
    if i2c:
        try:
            oled = ssd1306.SSD1306_I2C(w,h,i2c)
            print("SSD1306 initialized[Y]")  
            print('oled = ',oled)
            oled.rotate(False)
        except:
            print("failed to initialize onboard SSD1306")
    return oled

oled = initOLED()

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

def initNETWORK():
    global nm, SSID, Pass
    wifi_config = nm.wifi_connect(SSID, Pass)

def read_all(hall=hall):
    reads = []
    for i,x in enumerate(hall):
        reads.append(x.read())
        print(i,x,reads[-1])
    return(reads)
    
def calibrate(hall, start):
    global mins
    global maxes
    frint('RELEASE ALL')
    frint('Pistons')
    time.sleep(2)
    maxes = [x.read() for x in hall]
    frint('PRESS ALL Pistons')
    frint('Then Press Start BTN')

    # Wait for the start button to be pressed
    while start.value():
        pass

    mins = [x.read() for x in hall]
    # Save the calibration data to a file after calibration
    config['mins'] = mins
    config['maxes'] = maxes
    config.save()
    frint('Calibration complete and saved.')

def calculate_battery_percentage(batt_adc, ref_voltage=3.3, adc_max=4095):
    # Convert ADC reading to voltage
    voltage = (batt_adc.read() / adc_max) * ref_voltage
    
    # Define battery voltage range (adjust based on your battery specifications)
    max_battery_voltage = 4.2  # Voltage when battery is full
    min_battery_voltage = 3.0  # Voltage when battery is nearly empty

    # Map voltage to percentage (capping it between 0 and 100)
    if voltage >= max_battery_voltage:
        percentage = 100
    elif voltage <= min_battery_voltage:
        percentage = 0
    else:
        # Calculate percentage
        percentage = ((voltage - min_battery_voltage) / (max_battery_voltage - min_battery_voltage)) * 100

    return str(int(percentage)) + '%'  # Return rounded integer percentage

def taskbar(hall=hall, oled=oled, joystick_x=joystick_x, joystick_y=joystick_y, batt_level=batt_level):
    oled.fill(0)
    b = calculate_battery_percentage(batt_level)
    oled.text(f'OM-LASK4 {b}', 0, 0, 1)

    # Adjusted Joystick display area to be directly above the piston animation
    joystick_x_center = 107  # Centered above pistons (87 + 20 for half width of pistons area)
    joystick_y_center = 6    # Y position for joystick indicator (top of display)

    # Read joystick x and y values and normalize to fit in the display area
    joy_x = joystick_x.read()
    joy_y = joystick_y.read()
    joy_center = 2048  # Assuming 0-4095 range, center is around 2048
    
    # Calculate inverted offsets for display (scaling joystick movement)
    joy_offset_x = -int((joy_x - joy_center) / 500)  # Negate for inversion
    joy_offset_y = -int((joy_y - joy_center) / 500)

    # Limit the joystick range within the display area (40x14)
    joy_draw_x = max(87, min(joystick_x_center + joy_offset_x, 127))  # Limit within 40-pixel width at x=87
    joy_draw_y = max(0, min(joystick_y_center + joy_offset_y, 14))    # Inverted y direction

    # Draw joystick indicator (3x3 filled square to approximate a circle)
    oled.fill_rect(joy_draw_x, joy_draw_y, 3, 3, 1)

    # Draw pistons display area (unchanged from the previous position)
    x = 87
    y = 17
    global mins, maxes
    oled.fill_rect(x, y, 40, 14, 1)
    oled.fill_rect(x + 1, y + 1, 38, 12, 0)

    for i, z in enumerate(hall):
        div_top = (z.read() - mins[i])
        div_bottom = (maxes[i] - mins[i])
        if div_bottom == 0:
            div_bottom = 1
        ch = int((div_top / div_bottom) * 12)
        r_x = ((i + 1) * 7) + x
        r_y = 13 - ch + y
        oled.fill_rect(r_x, r_y, 5, ch, 1)
        oled.text(str(i + 1), i * 20, 16, 1)
        oled.text(str(ch * 8), i * 20, 24, 1)
    
    oled.show()


frint('OM-Labeler (LASK5)')

for i in range(1,5):
    temp = machine.ADC(machine.Pin(i))
    temp.atten(machine.ADC.ATTN_11DB)
    hall.append(temp)

cells = [hall[0],hall[1],hall[2],hall[3]]
read_all()

plen = 10
pi = 0
calib = [0,0,0,0]
blink(2)

def drawMenu():
    frint('OM-LASK5 Menu')


def fastRead():
    global nm, cells, mins, maxes
    
    # Configuration for multisampling
    BUFFER_SIZE = 10  # Number of ADC samples to average (adjust as needed)
    TARGET_RATE = 25  # Target sample rate in Hz
    SEND_INTERVAL = 1 / TARGET_RATE  # Time interval between sends in seconds
    
    # Initialize a buffer for each cell
    buffers = [[] for _ in range(len(cells))]
    last_send_time = time.ticks_ms()
    
    while True:
        # Collect samples as fast as possible
        for i in range(len(cells)):
            reading = cells[i].read() # - maxes[i]  # Adjust based on your calibration needs
            buffers[i].append(reading)
            
            # Maintain buffer size
            if len(buffers[i]) > BUFFER_SIZE:
                buffers[i].pop(0)
        
        # Check if it's time to send data
        now = time.ticks_ms()
        if time.ticks_diff(now, last_send_time) >= SEND_INTERVAL * 1000:
            # Average the buffer contents for each cell
            averaged_data = [sum(buffer) / len(buffer) if buffer else 0 for buffer in buffers]
            
            # Prepare the packet
            packet = {
                "id": "OM-LASK5",
                "ticks": now,
                "time": time.localtime(),
                "data": averaged_data
            }
            raw_data = str(packet)
            
            # Send the packet over UDP
            try:
                nm.udp_fast_send('192.168.1.48', 3145, raw_data)
                print('Sent:', raw_data)
            except Exception as e:
                print("Error sending data:", e)
            
            # Update the last send time
            last_send_time = now

        
def ESPNowSend():
    global nm, joystick_x, joystick_y,select, mins, maxes, peer
    while True:
        data_f = []
        for i in range(len(cells)):
            data_f.append(abs(cells[i].read()-mins[i]))
        data_f.append(joystick_x.read())
        nm.espnow_send(peer,str(data_f))
        #e.send(peer, str(data_f), True)
        if select.value() == 0:
            time.sleep(.1)
            nm.espnow_send(peer,b'end')
            #e.send(peer, b'end')
            frint('end')
            end = True
            return
        
def BraceletConnect():
    global nm, SSID, Pass, PCIP
    nm.espnow_send(peer,f'{SSID}:{Pass}:{CPIP}'.encode('utf-8'))
    #e.send(peer, f'{SSID}:{Pass}:{CPIP}')
    
def discoverPeers():
    global nm
    print('discover peers')
    
def mainMenu():
    global start, hall, select, up, down, oled, nm
    # Define the main menu and submenus as dictionaries
    menus = {
        'main': {
            'items': {
                0: {'label': '[0] Wifi Connect', 'action': initNETWORK},
                1: {'label': '[1] Calibration', 'submenu': 'calibration_menu'},
                2: {'label': '[2] Control', 'submenu': 'control_menu'},
                3: {'label': '[3] Network', 'submenu': 'network_menu'},
                4: {'label': '[4] Devices', 'submenu': 'device_menu'},
                5: {'label': '[5] Exit', 'action': lambda: 'exit'}  # Example for another item
            }
        },
        'calibration_menu': {
            'items': {
                0: {'label': '[0] Calibrate Controls', 'action': lambda: calibrate(hall, start)},
                1: {'label': '[1] Back', 'action': lambda: 'back'}
            }
        },
        'network_menu': {
            'items': {
                0: {'label': '[0] Wifi Connect', 'action': initNETWORK},
                1: {'label': '[1] Bracelet Connect', 'action': BraceletConnect},
                2: {'label': '[2] Back', 'action': lambda: 'back'}
            }
        },
        'control_menu': {
            'items': {
                0: {'label': '[0] UDP Send', 'action': fastReadLoop},
                1: {'label': '[1] ESPNow Send', 'action': ESPNowSend},
                2: {'label': '[2] Back', 'action': lambda: 'back'}
            }
        },
        'device_menu': {
            'items': {
                0: {'label': '[0] Discover Peers', 'action': discoverPeers},
                1: {'label': '[1] ESPNow Send', 'action': ESPNowSend},
                2: {'label': '[2] Back', 'action': lambda: 'back'}
            }
        }
    }
    current_menu = 'main'
    current_selection = 0
    visible_items = 4  # Number of items visible at a time
    scroll_position = 0  # Topmost visible item index
    exit_menu = False  # Flag to exit menu loop

    while not exit_menu:
        # Clear the OLED display
        oled.fill(0)

        # Calculate the current scroll position
        if current_selection >= scroll_position + visible_items:
            scroll_position = current_selection - visible_items + 1
        elif current_selection < scroll_position:
            scroll_position = current_selection

        # Draw the current menu items visible within the window
        for i in range(scroll_position, min(scroll_position + visible_items, len(menus[current_menu]['items']))):
            item = menus[current_menu]['items'][i]
            y_position = (i - scroll_position) * 8
            is_selected = (i == current_selection)
            if is_selected:
                oled.fill_rect(0, y_position, 128, 8, 1)  # Highlight background
                oled.text(item['label'], 0, y_position, 0)  # Inverted text on highlight
            else:
                oled.text(item['label'], 0, y_position, 1)  # Normal text

        # Draw a scroll indicator
        total_items = len(menus[current_menu]['items'])
        scroll_indicator_height = max(1, 32 * visible_items // total_items)
        scroll_indicator_position = 32 * scroll_position // total_items
        oled.fill_rect(127, scroll_indicator_position, 1, scroll_indicator_height, 1)

        oled.show()  # Update display

        # Button handling code remains the same
        # Start, Up, and Down button handling with debouncing

        # Check if start button is pressed for selection
        if start.value() == 0:
            selected_item = menus[current_menu]['items'][current_selection]

            # Check if item has a submenu or an action
            if 'submenu' in selected_item:
                # Move to the submenu
                current_menu = selected_item['submenu']
                current_selection = 0
                scroll_position = 0  # Reset scroll position when entering a submenu
                time.sleep(0.3)  # Debounce delay after entering submenu
            elif 'action' in selected_item:
                action_result = selected_item['action']()
                # Handle "Back" or "Exit" action results
                if action_result == 'back':
                    current_menu = 'main'
                    current_selection = 0
                    scroll_position = 0  # Reset scroll position when going back
                elif action_result == 'exit':
                    exit_menu = True  # Set flag to exit the menu loop
                    break  # Exit the loop immediately

            time.sleep(0.2)  # Debounce delay for 'start' button within submenu

        # Handle up/down button navigation
        if up.value() == 0 or down.value() == 0:
            menu_length = len(menus[current_menu]['items'])
            if up.value() == 0:
                current_selection = (current_selection - 1) % menu_length
            elif down.value() == 0:
                current_selection = (current_selection + 1) % menu_length

            # Debounce delay for navigation buttons
            time.sleep(0.3)

def fastReadLoop():
    """Loop for UDP send functionality; exits on select button press."""
    global nm
    nm.socket_open()
    while True:
        fastRead()
        if select.value() == 0:
            nm.socket_close()
            return
       
def mainloup(pi=pi,plen=plen,led=led,cells=cells,start=start,select=select,up=up,down=down,count=count):
    exit_bool = False
    button_thresh = 0
    while not exit_bool:
        if up.value() == 0 or down.value() == 0:
            mainMenu()
        if select.value() == 0:
            button_thresh += 1
        else:
            button_thresh += -1
        if button_thresh > 20:
            exit_bool = True
        elif button_thresh < 0:
            button_thresh = 0
        if pi == 0:
            frint('first run')
          #callibrate()
        if pi >= 10:
            taskbar()
            count += 1
            pi = 1
        else:
            pi += 1
    
if __name__ == "__main__":
    nm = NetworkManager()
    mainloup()
    




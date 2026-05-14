# OpenMuscle - OpenHand V2
# General-purpose hand controller firmware for ESP32-S2
# Receives finger data via ESP-NOW or UDP, drives PCA9685 servos

from machine import I2C, Pin
import time
import math
import network
import socket
import ssd1306
import gc
import espnow
import ujson

from pca9685 import PCA9685
from servo import Servos

# =============================================================================
# Configuration
# =============================================================================

# Pin assignments
LED_PIN = 15
SCL_PIN = 33
SDA_PIN = 34
START_PIN = 7
SELECT_PIN = 8
UP_PIN = 9
DOWN_PIN = 10

# OLED
OLED_WIDTH = 128
OLED_HEIGHT = 32

# Network
WIFI_SSID = 'OpenMuscle'
WIFI_PASS = '3141592653'
WIFI_TIMEOUT_S = 10
UDP_PORT = 3145

# Servo: finger index 0-4 maps to PCA9685 odd channels
FINGER_CHANNELS = [1, 3, 5, 7, 9]

# Sigmoid parameters
SIGMOID_K = 10
SIGMOID_MID = 0.5
SIGMOID_CLAMP = 20  # max abs exponent to prevent overflow

# Per-device configuration
# map: 'sigmoid' or 'linear'
# in_min/in_max: expected input value range
# reverse: whether to flip finger order ([::-1])
DEVICES = {
    'default': {'map': 'sigmoid', 'in_min': 0, 'in_max': 800, 'reverse': True},
    'L5':      {'map': 'sigmoid', 'in_min': 0, 'in_max': 800, 'reverse': True},
    'PC':      {'map': 'linear',  'in_min': 0, 'in_max': 179, 'reverse': False},
}

# =============================================================================
# Hardware init
# =============================================================================

led = Pin(LED_PIN, Pin.OUT)
start_btn = Pin(START_PIN, Pin.IN, Pin.PULL_UP)
select_btn = Pin(SELECT_PIN, Pin.IN, Pin.PULL_UP)
up_btn = Pin(UP_PIN, Pin.IN, Pin.PULL_UP)
down_btn = Pin(DOWN_PIN, Pin.IN, Pin.PULL_UP)

# Globals set during boot
i2c = None
oled = None
servo = None
ram = []


def blink(count):
    for _ in range(count):
        led.value(1)
        time.sleep(0.3)
        led.value(0)
        time.sleep(0.2)


def init_oled():
    global i2c, oled
    try:
        i2c = I2C(scl=Pin(SCL_PIN), sda=Pin(SDA_PIN))
        print('I2C scan:', i2c.scan())
    except Exception as err:
        print('I2C init failed:', err)
        return
    try:
        oled = ssd1306.SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, i2c)
        print('SSD1306 initialized')
    except Exception as err:
        print('SSD1306 init failed:', err)


def init_servos():
    global servo
    try:
        servo = Servos(i2c=i2c)
        print('Servos initialized')
    except Exception as err:
        print('Servo init failed:', err)


def frint(text):
    global ram
    text = str(text)
    if oled:
        if len(text) <= 16:
            ram.append(text)
        else:
            ram.append(text[:5] + '..' + text[-9:])
        oled.fill(0)
        for n, line in enumerate(ram[-4:]):
            oled.text(line, 0, n * 8)
        if len(ram) > 9:
            del ram[:-9]
        gc.collect()
        oled.show()
        print('f:>', ram[-1])
    else:
        print('f:<', text)


# =============================================================================
# Value mapping functions
# =============================================================================

def sigmoid_curve(x, in_min=0, in_max=800, out_min=0, out_max=179):
    if in_max == in_min:
        return out_min
    normalized = (x - in_min) / (in_max - in_min)
    exponent = -SIGMOID_K * (normalized - SIGMOID_MID)
    if exponent > SIGMOID_CLAMP:
        exponent = SIGMOID_CLAMP
    elif exponent < -SIGMOID_CLAMP:
        exponent = -SIGMOID_CLAMP
    sig = 1.0 / (1.0 + math.exp(exponent))
    return out_min + sig * (out_max - out_min)


def linear_map(x, in_min=0, in_max=179, out_min=0, out_max=179):
    if in_max == in_min:
        return out_min
    scaled = (x - in_min) / (in_max - in_min) * (out_max - out_min) + out_min
    return max(out_min, min(out_max, scaled))


def map_value(raw, cfg):
    if cfg['map'] == 'linear':
        return linear_map(raw, cfg['in_min'], cfg['in_max'])
    return sigmoid_curve(raw, cfg['in_min'], cfg['in_max'])


# =============================================================================
# Servo helpers
# =============================================================================

def set_finger(index, degrees):
    if servo and 0 <= index < len(FINGER_CHANNELS):
        ch = FINGER_CHANNELS[index]
        servo.position(index=ch, degrees=degrees)


def release_all():
    if servo:
        for ch in range(16):
            servo.release(ch)
        frint('All released')


# =============================================================================
# Packet parsing
# =============================================================================

def parse_packet(raw_bytes):
    """Parse packet as stringified list or CSV. Returns (device_id, [int_values]) or None on error."""
    try:
        text = raw_bytes.decode('utf-8').strip()
    except Exception:
        return None
    if not text:
        return None

    # Handle stringified list format like '[354, 556, 446, 664, 1945]'
    if text.startswith('[') and text.endswith(']'):
        try:
            values = ujson.loads(text)
            if isinstance(values, list) and all(isinstance(v, (int, float)) for v in values):
                return ('default', values)  # Assume default device for list format
        except ValueError:
            return None

    # Fallback to original CSV parsing
    parts = text.split(',')
    if not parts:
        return None
    # Check if first field is a device ID (non-numeric)
    first = parts[0].strip()
    try:
        int(first)
        # All numeric — bare values, use 'default'
        device_id = 'default'
        value_parts = parts
    except ValueError:
        # First field is device ID
        device_id = first
        value_parts = parts[1:]
    try:
        values = [int(v.strip()) for v in value_parts if v.strip() != '']
    except ValueError:
        return None
    if not values:
        return None
    return (device_id, values)


def apply_packet(device_id, values):
    """Look up device config, map values, drive servos."""
    cfg = DEVICES.get(device_id, DEVICES.get('default'))
    if cfg is None:
        return
    if cfg.get('reverse', False):
        values = values[::-1]
    for i, raw in enumerate(values):
        if i >= len(FINGER_CHANNELS):
            break
        deg = map_value(raw, cfg)
        set_finger(i, deg)


# =============================================================================
# Receive modes
# =============================================================================

def espnow_listen():
    frint('ESP-NOW init...')
    wlan_sta = network.WLAN(network.STA_IF)
    wlan_sta.active(False)
    wlan_sta.active(True)
    e = espnow.ESPNow()
    e.active(True)
    try:
        e.add_peer(b'\xff\xff\xff\xff\xff\xff')
    except Exception:
        pass
    frint('ESP-NOW ready')
    frint('SEL=exit')
    while True:
        # Non-blocking recv with 100ms timeout so buttons stay responsive
        msg = e.recv(100)
        if msg and msg[1]:
            print(f'msg[1]:{msg[1]}')
            result = parse_packet(msg[1])
            if result:
                device_id, values = result
                apply_packet(device_id, values)
        if select_btn.value() == 0:
            time.sleep(0.2)  # debounce
            break
    e.active(False)
    frint('ESP-NOW stopped')


def udp_listen():
    frint('WiFi connecting')
    wlan_sta = network.WLAN(network.STA_IF)
    wlan_sta.active(False)
    wlan_sta.active(True)
    time.sleep(0.5)
    wlan_sta.connect(WIFI_SSID, WIFI_PASS)
    t0 = time.time()
    while not wlan_sta.isconnected():
        if time.time() - t0 > WIFI_TIMEOUT_S:
            frint('WiFi timeout!')
            wlan_sta.active(False)
            return
        time.sleep(0.2)
    ip = wlan_sta.ifconfig()[0]
    frint('IP:' + ip)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('0.0.0.0', UDP_PORT))
    s.setblocking(False)
    frint('UDP :' + str(UDP_PORT))
    frint('SEL=exit')
    while True:
        try:
            data, addr = s.recvfrom(256)
            if data:
                result = parse_packet(data)
                if result:
                    device_id, values = result
                    apply_packet(device_id, values)
        except OSError:
            pass  # no data available (non-blocking)
        if select_btn.value() == 0:
            time.sleep(0.2)  # debounce
            break
    s.close()
    wlan_sta.disconnect()
    wlan_sta.active(False)
    frint('UDP stopped')


def servo_test():
    frint('Servo test...')
    if not servo:
        frint('No servo!')
        time.sleep(1)
        return
    for deg in [30, 90, 140, 90]:
        for i in range(len(FINGER_CHANNELS)):
            set_finger(i, deg)
        time.sleep(0.5)
    for i in range(len(FINGER_CHANNELS)):
        set_finger(i, 90)
    time.sleep(0.3)
    release_all()
    frint('Test done')


# =============================================================================
# Menu system
# =============================================================================

MENU_ITEMS = [
    'ESP-NOW Listen',
    'UDP Listen',
    'Servo Test',
    'Release All',
]

MENU_ACTIONS = [
    espnow_listen,
    udp_listen,
    servo_test,
    release_all,
]


def draw_menu(selected):
    if not oled:
        return
    oled.fill(0)
    for i, label in enumerate(MENU_ITEMS):
        if i == selected:
            oled.fill_rect(0, i * 8, OLED_WIDTH, 8, 1)
            oled.text(label, 0, i * 8, 0)
        else:
            oled.text(label, 0, i * 8, 1)
    oled.show()


def run_menu():
    selected = 0
    n_items = len(MENU_ITEMS)
    draw_menu(selected)
    while True:
        if up_btn.value() == 0:
            selected = (selected - 1) % n_items
            draw_menu(selected)
            time.sleep(0.25)
        if down_btn.value() == 0:
            selected = (selected + 1) % n_items
            draw_menu(selected)
            time.sleep(0.25)
        if start_btn.value() == 0:
            time.sleep(0.2)  # debounce
            MENU_ACTIONS[selected]()
            draw_menu(selected)
        time.sleep(0.05)


# =============================================================================
# Boot sequence
# =============================================================================

blink(3)
init_oled()
init_servos()
frint('OM-HAND V2')
blink(2)
run_menu()

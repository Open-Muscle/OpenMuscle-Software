# OpenMuscle - OpenHand V2
# General-purpose hand controller firmware for ESP32-S2
# Receives finger data via ESP-NOW or UDP, drives PCA9685 servos
#
# Origin: pulled verbatim from a working device on 2026-05-14 (archived at
# archive/openhand_v2_2026-05-14/) then patched with persistent settings.json
# + auto_mode so the device can boot straight into ESP-NOW or UDP receive
# without the operator navigating the menu first. Press Select while in
# auto-mode to fall back to the regular menu.

from machine import I2C, Pin
import time
import math
import network
import socket
import ssd1306
import gc
import espnow
import ujson
import os

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

# Network -- defaults; override via settings.json wifi_ssid / wifi_password.
# We connect to Wi-Fi at boot (regardless of auto_mode) so that:
#   1. ESPNow piggybacks the STA channel, which matches the modular LASK5
#      firmware's channel (it also connects to Wi-Fi before activating
#      ESPNow). Without this match, broadcasts from LASK5 never reach the
#      hand even though both 'see' the radio.
#   2. The PC's inference plug-in can forward predicted servo angles to
#      this hand over UDP at <hand_ip>:3145.
# Wi-Fi defaults are empty: never commit real SSID/password; the operator
# supplies them via settings.json (gitignored), and a fresh device boots
# straight to the menu since no STA join is possible. Mirrors the
# security remediation that landed for FlexGridV4 + LASK5.
WIFI_SSID_DEFAULT = ''
WIFI_PASS_DEFAULT = ''
WIFI_TIMEOUT_S = 10
UDP_PORT = 3145

# OpenMuscle protocol announce (PROTOCOL.md v1.0 section 5):
# OpenHand is a SINK (it receives servo commands rather than emitting
# sensor data) but the v1.0 protocol only has "source" role. We announce
# anyway so phone + PC hubs can discover the device in the same
# discovery UI as FlexGrid / LASK5; the caps list (`actuator`) and
# empty `services` map tell hubs there is no cmd channel to subscribe
# to. Servo commands continue on the existing UDP 3145 path.
# A future v1.1 spec may add a "sink" role; this lives there cleanly.
ANNOUNCE_PORT = 3140
ANNOUNCE_INTERVAL_S = 1.0
DEVICE_TYPE = 'openhand'
DEVICE_FW = 'v2.0.0'

# Servo: finger index 0-4 maps to PCA9685 odd channels
FINGER_CHANNELS = [1, 3, 5, 7, 9]

# Sigmoid parameters
SIGMOID_K = 10
SIGMOID_MID = 0.5
SIGMOID_CLAMP = 20  # max abs exponent to prevent overflow

# Per-device configuration (selected by packet 'device_id' prefix)
# map: 'sigmoid' or 'linear'
# in_min/in_max: expected input value range
# reverse: whether to flip finger order ([::-1])
DEVICES = {
    'default': {'map': 'sigmoid', 'in_min': 0, 'in_max': 800, 'reverse': True},
    'L5':      {'map': 'sigmoid', 'in_min': 0, 'in_max': 800, 'reverse': True},
    'PC':      {'map': 'linear',  'in_min': 0, 'in_max': 179, 'reverse': False},
}

# Persistent settings (loaded from /settings.json at boot; written when the
# user changes auto_mode via the menu). Keep this dict small and only add
# keys whose values must survive a reboot.
SETTINGS_PATH = 'settings.json'
SETTINGS_DEFAULTS = {
    # 'menu' (operator navigates each boot), 'espnow' (auto-enter ESP-NOW
    # listen on boot), 'udp' (auto-enter UDP listen on boot). Press Select
    # while in auto-mode to drop back to the menu.
    'auto_mode': 'menu',
    # Wi-Fi creds (per-device, never commit). Connected at boot regardless
    # of auto_mode so the ESPNow radio lives on the same channel as paired
    # devices and the hand's IP is reachable for PC inference forwarding.
    'wifi_ssid': WIFI_SSID_DEFAULT,
    'wifi_password': WIFI_PASS_DEFAULT,
    # Stable device id minted on first boot (openhand-<6hex from MAC>).
    # Re-used across reboots so hubs see a stable identity in announces.
    'device_id': '',
}
settings = dict(SETTINGS_DEFAULTS)

# Announce-broadcast state. Populated lazily after Wi-Fi is up.
_announce_sock = None
_announce_last_t = 0.0
_announce_device_id = None


def settings_load():
    global settings
    try:
        with open(SETTINGS_PATH, 'r') as f:
            data = ujson.load(f)
        # Backfill any new defaults so older settings.json files still work.
        merged = dict(SETTINGS_DEFAULTS)
        merged.update(data)
        settings = merged
    except Exception:
        settings = dict(SETTINGS_DEFAULTS)
        settings_save()


def settings_save():
    try:
        with open(SETTINGS_PATH, 'w') as f:
            ujson.dump(settings, f)
    except Exception as err:
        print('settings save failed:', err)


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


# Global Wi-Fi station, brought up once at boot. Both espnow_listen and
# udp_listen reuse it instead of bouncing STA active state.
wlan_sta = None


def _mint_device_id():
    """Mint or read the stable device id. openhand-<6 hex chars from MAC tail>.
    Persisted in settings on first boot so subsequent boots reuse it."""
    global _announce_device_id
    cur = settings.get('device_id') or ''
    if cur:
        _announce_device_id = cur
        return cur
    try:
        import binascii
        mac = network.WLAN(network.STA_IF).config('mac')
        tail = binascii.hexlify(mac[-3:]).decode()
        dev_id = 'openhand-' + tail
    except Exception:
        import time as _t
        dev_id = 'openhand-{:06x}'.format(_t.ticks_ms() & 0xFFFFFF)
    settings['device_id'] = dev_id
    settings_save()
    _announce_device_id = dev_id
    return dev_id


def maybe_announce():
    """Send one broadcast announce on UDP 3140 if at least
    ANNOUNCE_INTERVAL_S has passed since the last one. Cheap to call from
    inside the synchronous receive / menu loops at high rate; the
    timestamp throttle keeps the broadcast at ~1 Hz. No-op if Wi-Fi isn't
    up. PROTOCOL.md v1.0 section 5.2 announce shape.
    """
    global _announce_last_t, _announce_sock
    if wlan_sta is None or not wlan_sta.isconnected():
        return
    now = time.time()
    if now - _announce_last_t < ANNOUNCE_INTERVAL_S:
        return
    _announce_last_t = now
    if _announce_sock is None:
        try:
            _announce_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _announce_sock.setblocking(False)
            try:
                _announce_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except Exception:
                pass
        except Exception as err:
            print('announce sock init failed:', err)
            return
    payload = {
        'v':          '1.0',
        'type':       'announce',
        'id':         _announce_device_id or 'openhand-?',
        'role':       'source',     # v1.0 protocol only has "source"; "sink" is v1.1+
        'dev':        DEVICE_TYPE,
        'fw':         DEVICE_FW,
        'transports': ['wifi'],
        'caps':       ['actuator'], # tells hubs this is a destination for commands,
                                    # not a source of sensor data; no cmd channel today
        'services':   {},           # no cmd port to subscribe to in v2.0.0
        'ts':         time.ticks_ms(),
    }
    try:
        _announce_sock.sendto(ujson.dumps(payload).encode('utf-8'),
                              ('255.255.255.255', ANNOUNCE_PORT))
    except OSError:
        # ENOMEM / EAGAIN on a busy lwip pbuf pool; non-fatal, retry next tick.
        pass
    except Exception as err:
        print('announce send failed:', err)


def connect_wifi_boot():
    """Connect to the configured Wi-Fi at boot. Non-fatal on failure --
    auto-mode dispatch proceeds either way."""
    global wlan_sta
    wlan_sta = network.WLAN(network.STA_IF)
    wlan_sta.active(True)
    ssid = settings.get('wifi_ssid', WIFI_SSID_DEFAULT)
    pw = settings.get('wifi_password', WIFI_PASS_DEFAULT)
    if not ssid:
        frint('No wifi cfg')
        return
    if wlan_sta.isconnected():
        # Already connected (rare on cold boot, but possible after a soft
        # reset that preserved STA state)
        frint('IP:' + wlan_sta.ifconfig()[0])
        return
    frint('WiFi ' + ssid)
    try:
        wlan_sta.connect(ssid, pw)
    except Exception as err:
        frint('WiFi err')
        print('connect raised:', err)
        return
    t0 = time.time()
    while not wlan_sta.isconnected():
        if time.time() - t0 > WIFI_TIMEOUT_S:
            frint('WiFi timeout')
            return
        time.sleep(0.2)
    ip = wlan_sta.ifconfig()[0]
    frint('IP:' + ip)
    # Brief pause so the operator can read the IP off the OLED before
    # auto-mode dispatch overwrites it
    time.sleep(1.0)


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
    # Don't bounce STA active state -- we want to keep the boot-time Wi-Fi
    # connection up so ESPNow uses the same channel as our paired devices
    # (the modular LASK5 firmware activates ESPNow after WiFi connect too).
    global wlan_sta
    if wlan_sta is None:
        wlan_sta = network.WLAN(network.STA_IF)
        wlan_sta.active(True)
    e = espnow.ESPNow()
    e.active(True)
    try:
        e.add_peer(b'\xff\xff\xff\xff\xff\xff')
    except Exception:
        pass
    frint('ESP-NOW ready')
    frint('SEL=exit')
    try:
        while True:
            # Non-blocking recv with 100ms timeout so buttons stay responsive
            msg = e.recv(100)
            if msg and msg[1]:
                print('msg[1]:' + str(msg[1]))
                result = parse_packet(msg[1])
                if result:
                    device_id, values = result
                    apply_packet(device_id, values)
            # Throttled to ~1 Hz internally; cheap to call here.
            maybe_announce()
            if select_btn.value() == 0:
                time.sleep(0.2)  # debounce
                break
    except KeyboardInterrupt:
        frint('ESPNow -> REPL')
        try: release_all()
        except Exception: pass
        raise
    finally:
        try: e.active(False)
        except Exception: pass
    frint('ESP-NOW stopped')


# Idle-sleep threshold: after this many seconds with no incoming UDP packet
# we release all servos. They stop humming/holding torque, and the OLED
# shows "Sleeping..." so the operator knows the hand is intentionally
# limp (not crashed). The next packet wakes it up — set_finger() implicitly
# re-energizes the servo, so wake is just "go back to applying packets".
UDP_IDLE_SLEEP_S = 30


def udp_listen():
    # Reuse the boot-time STA connection. If for some reason we don't have
    # one yet (auto_mode=menu user navigated here manually before boot
    # connect_wifi_boot ran), reconnect using current settings.
    global wlan_sta
    if wlan_sta is None or not wlan_sta.isconnected():
        connect_wifi_boot()
    if wlan_sta is None or not wlan_sta.isconnected():
        frint('No wifi!')
        time.sleep(1.5)
        return
    ip = wlan_sta.ifconfig()[0]
    frint('IP:' + ip)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('0.0.0.0', UDP_PORT))
    s.setblocking(False)
    frint('UDP :' + str(UDP_PORT))
    frint('SEL=exit')

    last_packet_t = time.time()  # grace period: 30s before first sleep
    is_asleep = False

    # KeyboardInterrupt-aware loop: a Ctrl-C from the REPL (mpremote, Thonny,
    # serial monitor) now exits this loop cleanly so the operator can land
    # in the REPL and edit settings / firmware without having to power-cycle.
    try:
        while True:
            got_packet = False
            try:
                data, addr = s.recvfrom(256)
                if data:
                    got_packet = True
                    result = parse_packet(data)
                    if result:
                        device_id, values = result
                        apply_packet(device_id, values)
            except OSError:
                pass  # no data available (non-blocking)

            if got_packet:
                last_packet_t = time.time()
                if is_asleep:
                    # Wake transition: brief OLED note, then resume listening.
                    # apply_packet() above already drove this packet's values,
                    # which implicitly re-energized the servos -- no extra work.
                    is_asleep = False
                    frint('Awake')
            else:
                # No packet this iteration -- check idle timeout.
                if not is_asleep and (time.time() - last_packet_t) > UDP_IDLE_SLEEP_S:
                    release_all()           # stop all 16 channels (servos go limp)
                    is_asleep = True
                    frint('Sleeping')

            # Throttled to ~1 Hz internally; cheap to call from the tight
            # awake loop AND the 20 Hz asleep loop.
            maybe_announce()

            if select_btn.value() == 0:
                time.sleep(0.2)  # debounce
                break

            # Small yield so the non-blocking recv loop doesn't peg the CPU
            # (also lets the REPL Ctrl-C have a chance to interrupt).
            if is_asleep:
                time.sleep(0.05)        # asleep: be lazy, 20 Hz wake-check
            else:
                time.sleep(0.002)       # awake: tight loop, ~500 Hz
    except KeyboardInterrupt:
        frint('UDP -> REPL')
        try: release_all()
        except Exception: pass
        # Re-raise so the outer boot-sequence handler can also drop cleanly.
        raise
    finally:
        try: s.close()
        except Exception: pass

    # Keep STA connected on exit so ESPNow still has its channel locked.
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
# Auto-mode management
# =============================================================================

# Order of cycling for the 'Auto: ...' menu item: each Start press advances.
AUTO_MODES = ('menu', 'espnow', 'udp')


def cycle_auto_mode():
    cur = settings.get('auto_mode', 'menu')
    try:
        i = AUTO_MODES.index(cur)
    except ValueError:
        i = 0
    new = AUTO_MODES[(i + 1) % len(AUTO_MODES)]
    settings['auto_mode'] = new
    settings_save()
    frint('Auto: ' + new)
    time.sleep(0.4)


# =============================================================================
# Menu system
# =============================================================================

# Static labels; the 'Auto: ...' label is patched in render to show the
# current value.
MENU_ITEMS = [
    'ESP-NOW Listen',
    'UDP Listen',
    'Servo Test',
    'Release All',
    'Auto: ?',  # rendered as 'Auto: <current>'
]

MENU_ACTIONS = [
    espnow_listen,
    udp_listen,
    servo_test,
    release_all,
    cycle_auto_mode,
]


def menu_label(i):
    if MENU_ITEMS[i].startswith('Auto:'):
        return 'Auto:' + settings.get('auto_mode', 'menu')
    return MENU_ITEMS[i]


def draw_menu(selected):
    if not oled:
        return
    oled.fill(0)
    for i in range(len(MENU_ITEMS)):
        label = menu_label(i)
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
    try:
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
            # Keep announcing while the operator browses the menu so the
            # device stays discoverable even before a listen mode is entered.
            maybe_announce()
            time.sleep(0.05)
    except KeyboardInterrupt:
        frint('Menu -> REPL')
        try: release_all()
        except Exception: pass
        raise


# =============================================================================
# Boot sequence
# =============================================================================

settings_load()
blink(3)
init_oled()
init_servos()
frint('OM-HAND V2')

# Bring Wi-Fi up before auto-mode dispatch. ESPNow uses the same radio,
# so a connected STA pins the channel to match paired devices. Failure is
# non-fatal -- device continues to work via menu/USB even without Wi-Fi.
connect_wifi_boot()

# Mint/load the OpenMuscle device id and announce ourselves on UDP 3140
# so the phone + PC hubs discover us in the V4 discovery UI. The id is
# stable across reboots (persisted to settings.json on first mint).
# Hub-side: caps=['actuator'] + services={} tells consumers there is no
# cmd channel; existing servo commands keep flowing on UDP 3145 below.
_mint_device_id()

# Hold Select at boot to force the menu, regardless of auto_mode -- escape
# hatch in case auto-mode is set to something that crashes or hangs.
#
# The whole runtime is wrapped in try/except KeyboardInterrupt so a Ctrl-C
# from the REPL (mpremote, Thonny, serial monitor) drops back to an
# interactive prompt cleanly. Without this, the device locks out the host
# while a listen loop is running and we can't edit firmware/settings
# without a physical power-cycle. Servos are released on the way out so
# fingers don't hold torque while you're hacking.
try:
    if select_btn.value() == 0:
        frint('Auto skipped')
        time.sleep(0.5)
    else:
        mode = settings.get('auto_mode', 'menu')
        if mode == 'espnow':
            frint('Auto: ESP-NOW')
            time.sleep(0.4)
            espnow_listen()  # returns when Select pressed
        elif mode == 'udp':
            frint('Auto: UDP')
            time.sleep(0.4)
            udp_listen()     # returns when Select pressed

    blink(2)
    run_menu()
except KeyboardInterrupt:
    try: release_all()
    except Exception: pass
    frint('REPL')
    print('\nCtrl-C received -- dropped to REPL.')
    print('To resume: run_menu()  or  exec(open("boot.py").read())')

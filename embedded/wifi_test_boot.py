import network
import time

# Wi-Fi credentials
SSID = "OpenMuscle"
PASSWORD = "3141592653"

ap = network.WLAN(network.AP_IF)
if ap.active():
    print("Disabling AP…")
    ap.active(False)

wlan = network.WLAN(network.STA_IF)


# print(wlan.ifconfig())
# print(f'wlan.active():{wlan.active()}')

if wlan.isconnected():
    wlan.disconnect()
wlan.active(False)
time.sleep(0.5)
wlan.active(True)
# Try scanning multiple times in case the first few return empty
for _ in range(5):
    nets = wlan.scan()
    if nets:
        break
    time.sleep(0.5)
print("Found networks:", nets)

# assume you already did:
# wlan = network.WLAN(network.STA_IF)
# wlan.active(True)
# nets = wlan.scan()

for net in nets:
    ssid, bssid, channel, rssi, authmode, hidden = net

    # decode the SSID bytes into a string (empty if hidden SSID)
    ssid_str = ssid.decode('utf-8') or '<hidden>'

    # format the MAC address in hex
    bssid_str = ':'.join(f'{b:02x}' for b in bssid)

    print(
        f"SSID: {ssid_str}, "
        f"BSSID: {bssid_str}, "
        f"Channel: {channel}, "
        f"RSSI: {rssi} dBm, "
        f"Authmode: {authmode}, "
        f"Hidden: {hidden}"
    )

def connect(ssid, password, timeout=1000):
    wlan = network.WLAN(network.STA_IF)
    # If already connected, tear it down first
    if wlan.isconnected():
        print("Already connected; disconnecting…")
        wlan.disconnect()
        time.sleep(1)

    # Fully reset the interface
    wlan.active(False)
    time.sleep(0.5)
    wlan.active(True)

    print(f"Connecting to {ssid!r}…")
    print("SSID repr:", repr(SSID))
    print("PASS repr:", repr(PASSWORD))
    print("PASS length:", len(PASSWORD))
    wlan.connect(ssid, password, bssid=bytes.fromhex('b2bbfbbd16ac'))

    # Wait up to `timeout` seconds
    for i in range(timeout):
        if wlan.isconnected():
            print("✅ Connected!")
            print("Network config:", wlan.ifconfig())
            return True
        print(f"  …waiting ({i+1}/{timeout})")
        st = wlan.status()
        if st == network.STAT_CONNECTING:
            print("…connecting")
        elif st == network.STAT_GOT_IP:
            print("✅ Got IP!", wlan.ifconfig())
            return True
        else:
            print("⚠️ status:", st)
        time.sleep(.1)

    print("❌ Failed to connect.")
    return False


if __name__ == "__main__":
    success = connect(SSID, PASSWORD)
    if not success:
        # Optionally retry or handle error
        pass

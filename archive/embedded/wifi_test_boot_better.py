import network
import time

# Wi-Fi credentials
SSID = "IDFK"
PASSWORD = "bluebonnet!"

ap = network.WLAN(network.AP_IF)
if ap.active():
    print("Disabling AP…")
    ap.active(False)

wlan = network.WLAN(network.STA_IF)

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

# Print scanned networks in a cleaner format
print("Found networks:")
if nets:
    print("SSID\t\tBSSID\t\tChannel\tRSSI (dBm)\tAuthmode\tHidden")
    for net in nets:
        ssid, bssid, channel, rssi, authmode, hidden = net
        ssid_str = ssid.decode('utf-8') or '<hidden>'
        bssid_str = ':'.join(f'{b:02x}' for b in bssid)
        print(f"{ssid_str}\t{bssid_str}\t{channel}\t{rssi}\t\t{authmode}\t\t{hidden}")
else:
    print("No networks found.")

def connect(ssid, password, timeout=100, verbose=False):
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
    print("SSID repr:", repr(ssid))
    print("PASS repr:", repr(password))
    print("PASS length:", len(password))
    wlan.connect(ssid, password)  # <-- Added this back!

    start_time = time.time()
    last_status = None
    phase_start = start_time
    while time.time() - start_time < timeout:
        current_status = wlan.status()
        elapsed = time.time() - phase_start

        if wlan.isconnected():
            print("✅ Connected!")
            print("Network config:", wlan.ifconfig())
            return True

        if current_status == network.STAT_GOT_IP:
            print("✅ Got IP!", wlan.ifconfig())
            return True

        # Print only on status change or every 1s
        if current_status != last_status or elapsed >= 1 or verbose:
            if last_status is not None and not verbose:
                phase_duration = round(elapsed, 1)
                print(f"  …{get_status_desc(last_status)} for {phase_duration}s")
            if current_status == network.STAT_CONNECTING:
                print("…connecting")
            else:
                print(f"⚠️ Status: {get_status_desc(current_status)} ({current_status})")
            last_status = current_status
            phase_start = time.time()

        time.sleep(0.1)

    # Summarize final phase
    if last_status is not None:
        final_duration = round(time.time() - phase_start, 1)
        print(f"  …{get_status_desc(last_status)} for {final_duration}s")

    print(f"❌ Failed to connect after {timeout}s.")
    return False

def get_status_desc(status):
    descriptions = {
        -3: "No AP found",
        -2: "Wrong password",
        -1: "Fail",
        0: "Idle",
        1: "Connecting",
        2: "Wrong password",  # Note: 202 in output likely maps to this; adjust if your device uses different codes
        3: "No AP",
        1000: "Idle/no activity",
        1001: "Connecting",
        1010: "Connected",
        2022: "Association failed",
        2023: "Handshake failed",
        # Add more if needed from your device's docs
    }
    return descriptions.get(status, "Unknown")

if __name__ == "__main__":
    success = connect(SSID, PASSWORD)
    if not success:
        # Optionally retry or handle error
        pass
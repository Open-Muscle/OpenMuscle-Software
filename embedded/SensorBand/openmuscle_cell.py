# openmuscle_cell.py 
# Hardware: Open Muscle Dev Kit V0
# Version: 1.1.0
# 4/24/2024 - TURFPTAx

from settings_manager import SettingsManager
from network_manager import NetworkManager
import time
from machine import ADC, Pin

### Load Configuration Settings & Persistant Memory
defaults = {
    'calibration_data': [],
    'sensor_threshold': 0.5,
    'SSID':'OpenMuscle',
    'Pass':'3141592653',
    'PCIP': '192.168.1.48',
    'device_name': 'OpenMuscle Sensor',
    'ledPIN' : 8,
    'led' : False
}

# Initialize SettingsManager
config = SettingsManager(defaults=defaults)

if config.is_first_run:
    # First run setup
    print("First run detected. Performing initial setup...")

    # Perform calibration (replace with your own function)
    def perform_calibration():
        # Dummy calibration data
        return [0.1, 0.2, 0.3]

    calibration_data = perform_calibration()
    config['calibration_data'] = calibration_data

    # Save initial settings
    config.save()
else:
    print("Loaded saved settings.")

# Define local variables from settings
ledPIN = config['ledPIN']
led = config['led']
SSID = config['SSID']
Pass = config['Pass']
PCIP = config['PCIP']


# Code feedback through onboard LED GPIO 15 or ledPIN
def initLED(ledPIN):
    try:
        led = Pin(ledPIN,Pin.OUT)
        print('led initialized!')
        print('led =',led)
        try:
            throw(1)
        except:
            print('couldnt use throw(1)')
    except:
        print('led did not work :(')
        led = False
    return(led)

def throw(amt, led=led):
    if led:
        for i in range(amt):
            led.value(1)
            time.sleep(.66)
            led.value(0)
            time.sleep(.33)
    else:
        print('throw(n) n=',amt)

def initNETWORK():
    global nm, SSID, Pass
    wifi_config = nm.wifi_connect(SSID, Pass)


def calibrate(data):
    calib = []
    for x in data:
        calib.append(int(x))
    return calib

def fastRead():
    global nm, cells
    packet = {}
    data = []
    for i in range(len(cells)):
        data.append(cells[i].read()-calib[i])
    packet['id'] = 'OM-BraceletV1'
    packet['ticks'] = time.ticks_ms()
    packet['time'] = time.localtime()
    #Append the cycle with : deliminer delimeter
    packet['data'] = data
    raw_data = str(packet).encode('utf-8')
    try:
        nm.udp_fast_send('192.168.1.48',3145,raw_data)
        status = str(packet)
    except:
        status = 'failed'
    #return(status)

def fastReadLoop():
    """Loop for UDP send functionality; exits on select button press."""
    global nm
    nm.socket_open()
    while True:
        fastRead()
        if select.value() == 0:
            nm.socket_close()
            return
        

def mainloop():
    global nm, calib, pi, plen, led, cells
    # Main loop
    while True:
        # Check for incoming messages
        message = nm.get_next_message()
        if message:
            mac_address, recv_msg = message
            print('recv_msg',recv_msg)
            if recv_msg.startswith("HELLO_ACK"):
                _, device_id = recv_msg.split(":")
                print(f"Received HELLO_ACK from {device_id}")
                known_peers.append(mac_address)
            elif recv_msg.startswith("WIFI_CREDENTIALS:"):
                encrypted_credentials = recv_msg.split(":", 1)[1].encode()
                credentials = nm.xor_decrypt(encrypted_credentials, nm.encryption_key).decode()
                ssid, password = credentials.split("|")
                print(f"Received Wi-Fi credentials: SSID={ssid}, Password={password}")
                # Connect to Wi-Fi
                nm.wifi_connect(ssid, password)
            elif recv_msg.startswith("HEALTH_STATUS:"):
                status_data = recv_msg.split(":", 1)[1]
                battery_level, memory_usage = status_data.split("|")
                print(f"Health Status from {mac_address}: Battery={battery_level}%, Memory={memory_usage} bytes")
            elif recv_msg.startswith("TASK_STATUS:"):
                status_info = recv_msg.split(":", 1)[1]
                task_id, status = status_info.split("|")
                print(f"Task {task_id} on {mac_address} is {status}")
            elif recv_msg == "HELLO":
                # Send HELLO_ACK back to the sender
                nm.espnow_send(mac_address, f"HELLO_ACK:{nm.device_id}")
            elif recv_msg == "REQUEST_WIFI":
                # Send Wi-Fi credentials
                ssid = "YourSSID"
                password = "YourPassword"
                credentials = f"{ssid}|{password}".encode()
                encrypted_credentials = nm.xor_encrypt(credentials, nm.encryption_key)
                nm.espnow_send(mac_address, "WIFI_CREDENTIALS:".encode() + encrypted_credentials)
            elif recv_msg == "HEALTH_CHECK":
                # Gather health data
                battery_level = nm.get_battery_level()
                memory_usage = nm.get_memory_usage()
                status_data = f"{battery_level}|{memory_usage}"
                nm.espnow_send(mac_address, f"HEALTH_STATUS:{status_data}")
            elif recv_msg.startswith("EXECUTE_TASK:"):
                _, task_id = recv_msg.split(":")
                # Start the task
                nm.start_task(task_id)
                # Send TASK_STATUS back
                nm.espnow_send(mac_address, f"TASK_STATUS:{task_id}|STARTED")
                # Simulate task completion
                time.sleep(2)  # Simulate task duration
                nm.espnow_send(mac_address, f"TASK_STATUS:{task_id}|COMPLETED")
            else:
                print(f"Received message from {mac_address}: {recv_msg}")
        else:
            # No message, perform other tasks or sleep
            pass

        # Example: Send health check to known peers
        for peer_mac in known_peers:
            nm.send_health_check(peer_mac)
            nm.send_execute_task(peer_mac, task_id="1")

        time.sleep(5)  # Adjust sleep time as needed

def getNTPtime():
    try:
        import ntptime
        ntptime.settime()
        time.localtime()
        print(time.localtime())
    except:
        print('failed to set NTP time')
        

if __name__ == "__main__":
    #Setup basic ADC pin read array test
    led = initLED(ledPIN)
    print('hall array: hall[0-1]')
    hall = []
    # Pins 0, 1, 3, and 4 on the C3 SuperMini
    for i in range(6):
        if i != 2 and i != 4:
            temp = ADC(Pin(i))
            #important to read the value properly
            temp.atten(ADC.ATTN_11DB)
            hall.append(temp)
    for i,x in enumerate(hall):
      print(i,x)

    print('hall array setup[Y]')
    throw(5)
    #Gather send
    #Declare temp var to write to text file
    cells = [hall[0],hall[1],hall[2],hall[3]]
    #packet length
    plen = 10
    #packet iterator
    pi = 0
    calib = [0,0,0,0]

    #stop white loop for 10
    time.sleep(10)
    nm = NetworkManager()
    # Connect to Wi-Fi (if necessary)
    try:
        wifi_config = nm.wifi_connect(SSID, Pass)
    except:
        wifi_config = False
    if wifi_config:
        print("Connected to Wi-Fi successfully!")
    else:
        print("Failed to connect to Wi-Fi.")

    known_peers = []  # This should be populated with actual peer MAC addresses
    mainloop()



# openmuscle_cell.py 4/24/2024
# Hardware: Open Muscle Dev Kit V0
# UDP send 
# 2 x 1
# TURFPTAx


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
ledPin = config['ledPIN']
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

led = initLED(ledPIN)

def throw(amt, led=led):
    if led:
        for i in range(amt):
            led.value(1)
            time.sleep(.66)
            led.value(0)
            time.sleep(.33)
    else:
        print('throw(n) n=',amt)

#Setup basic ADC pin read array test
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

def initNETWORK():
    global nm, SSID, Pass
    wifi_config = nm.wifi_connect(SSID, Pass)

cells = [hall[0],hall[1],hall[2],hall[3]]


def calibrate(data):
    calib = []
    for x in data:
        calib.append(int(x))
    return calib

try:
    import ntptime
    ntptime.settime()
    time.localtime()
    print(time.localtime())
except:
    print('failed to set NTP time')

#Gather send
#Declare temp var to write to text file

#packet length
plen = 10
#packet iterator
pi = 0
calib = [0,0,0,0]

#stop white loop for 10
time.sleep(10)

              #
    #
def mainloup(calib=calib,pi=pi,plen=plen,led=led,cells=cells):
    exit_bool = False
    button_thresh = 0
    while not exit_bool:
        packet = {}
        data = []
        for i in range(len(cells)):
            data.append(cells[i].read()-calib[i])
        packet["id"] = "OM-Band12"
        packet["ticks"] = time.ticks_ms()
        packet["time"] = time.localtime()
        if pi == 0:
            print("No calibration just raw data")
                  #calib = calibrate(data)
                  #print(calib)
        if pi >= 10:
            pi = 1
        else:
            pi += 1
                #Append the cycle with : deliminer delimeter
        packet['data'] = data
        raw_data = str(packet).encode('utf-8')
        try:
                  #UDP recepient address
                  #Work on dynamic setup protocol
                  #s.sendto(raw_data,('192.168.1.32',3145))
            print(raw_data)
        except:
            print('failed')


if __name__ == "__main__":
    nm = NetworkManager()
    mainloup()

# This file is executed on every boot (including wake-boot from deepsleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()

from settings_manager import SettingsManager
import time


# Define default settings
defaults = {
    'device_name': 'OpenMuscle Sensor',
    'sensor_mapping': False,
    'device_mac': False,
    'ledPIN' = 8,
    'hallGPIOs' = [0,1,3,5]
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

## Access settings examples
#sensor_threshold = config['sensor_threshold']
#calibration_data = config['calibration_data']
#device_name = config['device_name']

#print(f"Device Name: {device_name}")
#print(f"Sensor Threshold: {sensor_threshold}")
#print(f"Calibration Data: {calibration_data}")

# Main loop or application logic
# Simplify machine and from machine reduntant
from machine import ADC, Pin
import socket
import network


ledPIN = config['ledPIN']
led = False

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
hallGPIOs = config['hallGPIOs']
for i in hallGPIOs:
    temp = ADC(Pin(i))
    #important to read the value properly
    temp.atten(ADC.ATTN_11DB)
    hall.append(temp)

for i,x in enumerate(hall):
  print(i,x)

print('hall array setup[Y]')
throw(5)

def initNETWORK():
  #need optional backup UDP repl if can't connect
  #primary and secondary networks
  #if primary then try secondary dev wifi access point
  wlan = network.WLAN(network.STA_IF) 
  wlan.active(False)
  if not wlan.isconnected():
    print('connecting to network...')
    if wlan.isconnected() == False:
      wlan.active(True)
    wlan.connect('OpenMuscle','3141592653')
    while not wlan.isconnected():
      pass

  print('assing port and bind')
  port = 3145
  print('network config: ',wlan.ifconfig())
  s = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
  return(s,wlan)
#s.bind(('192.168.103.203',port))

#s,wlan = initNETWORK()

# Need a Sensor Band Setup Process
# Maps the sensors to their location
cells = [hall[0],hall[1],hall[2],hall[3]]

#inital hall sensor ADC calibration
# grabs first few inputs and reduces the value 

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

mainloup()


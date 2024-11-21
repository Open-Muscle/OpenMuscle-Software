import socket
import sys
import os
import time
import ast
import threading
from queue import Queue

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore

# Constants for sample range
MAX_SAMP = 800
MIN_SAMP = -50

def get_local_ip_address():
    """Retrieve the local IP address of the computer."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to an external host to get the local IP address
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    finally:
        s.close()
    return local_ip

def parse_args():
    """Parse command-line arguments for IP and port."""
    if len(sys.argv) == 3:
        ip = sys.argv[1]
        port = int(sys.argv[2])
    else:
        print('Usage: python UDPserver.py <server ip> <UDP port>')
        ip = get_local_ip_address()
        port = 3145
        print(f'Using {ip} and port {port}')
    return ip, port

def create_udp_socket(ip, port):
    """Create and bind a UDP socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((ip, port))
    print(f'Server Address: {ip} Port: {port}')
    print('Press Ctrl+C to exit the program!')
    return sock

def setup_data_file():
    """Set up the data file for saving captured packets."""
    data_dir = 'Data-Captures'
    os.makedirs(data_dir, exist_ok=True)
    filenumber = len(os.listdir(data_dir))
    filepath = os.path.join(data_dir, f'capture_{filenumber}.txt')
    data_file = open(filepath, 'w')
    return data_file

def packet_receiver(sock, packet_queue):
    """Thread function to receive packets and put them in a queue."""
    while True:
        try:
            data, address = sock.recvfrom(4096)
            text = data.decode('utf-8')
            # Use ast.literal_eval to parse the data
            packet = ast.literal_eval(text)
            packet_queue.put(packet)
        except socket.error:
            pass  # No data received
        except Exception as e:
            print(f"Error parsing packet: {e}")
            print(f"Data was: {text}")

def main():
    """Main function to run the UDP server and PyQtGraph visualization."""
    ip, port = parse_args()
    sock = create_udp_socket(ip, port)
    data_file = setup_data_file()

    packet_queue = Queue()

    receiver_thread = threading.Thread(target=packet_receiver, args=(sock, packet_queue))
    receiver_thread.daemon = True
    receiver_thread.start()

    # PyQtGraph setup
    app = pg.mkQApp()
    win = pg.GraphicsLayoutWidget(show=True, title="OpenMuscle Data Visualization")
    win.resize(1000, 800)
    win.setWindowTitle('OpenMuscle Data Visualization')

    pg.setConfigOptions(antialias=True)

    # Initialize devices dictionary
    devices = {
        'OM-LASK5': {'plot': None, 'data': [[] for _ in range(4)], 'curves': []},
        'SensorBand': {'plot': None, 'data': [[] for _ in range(12)], 'curves': []}
    }

    # Set up plots and curves
    colors = [
        (255, 0, 0),    # Red
        (0, 255, 0),    # Green
        (0, 0, 255),    # Blue
        (255, 255, 0),  # Yellow
        (255, 0, 255),  # Magenta
        (0, 255, 255),  # Cyan
        (128, 0, 0),    # Maroon
        (0, 128, 0),    # Dark Green
        (0, 0, 128),    # Navy
        (128, 128, 0),  # Olive
        (128, 0, 128),  # Purple
        (0, 128, 128)   # Teal
    ]

    # Set up OM-LASK5 plot
    devices['OM-LASK5']['plot'] = win.addPlot(title='OM-LASK5')
    num_channels = 4
    for i in range(num_channels):
        curve = devices['OM-LASK5']['plot'].plot(pen=pg.mkPen(color=colors[i % len(colors)], width=2), name=f'Channel {i+1}')
        devices['OM-LASK5']['curves'].append(curve)
        devices['OM-LASK5']['data'][i] = []

    # Set up SensorBand plot
    win.nextRow()
    devices['SensorBand']['plot'] = win.addPlot(title='SensorBand')
    num_sensors = 12
    for i in range(num_sensors):
        curve = devices['SensorBand']['plot'].plot(pen=pg.mkPen(color=colors[i % len(colors)], width=2), name=f'Sensor {i}')
        devices['SensorBand']['curves'].append(curve)
        devices['SensorBand']['data'][i] = []

    t0 = time.time()
    timer = QtCore.QTimer()

    def update():
        """Update function called by the timer."""
        while not packet_queue.empty():
            packet = packet_queue.get()
            packet['rec_time'] = time.time() - t0
            data_file.write(str(packet) + '\n')
            device_id = packet['id']
            if device_id == 'OM-LASK5':
                # Process LASK5 data
                device = devices['OM-LASK5']
                # Append new data
                for i, value in enumerate(packet['data']):
                    device['data'][i].append(value)
                    if len(device['data'][i]) > 1000:
                        device['data'][i] = device['data'][i][-1000:]
                # Update curves
                for i, curve in enumerate(device['curves']):
                    y_data = device['data'][i]
                    x_data = list(range(len(y_data)))
                    curve.setData(x_data, y_data)
            elif device_id.startswith('OM-SB-V1-C.'):
                # Process bracelet data
                sensor_band = devices['SensorBand']
                hall_index = packet.get('hallIndex', [])
                data_values = packet.get('data', [])
                for index, value in zip(hall_index, data_values):
                    if 0 <= index < 12:
                        sensor_band['data'][index].append(value)
                        if len(sensor_band['data'][index]) > 1000:
                            sensor_band['data'][index] = sensor_band['data'][index][-1000:]
                # Update curves
                for i, curve in enumerate(sensor_band['curves']):
                    y_data = sensor_band['data'][i]
                    if y_data:
                        x_data = list(range(len(y_data)))
                        curve.setData(x_data, y_data)
            else:
                # Handle other devices if necessary
                pass

    timer.timeout.connect(update)
    timer.start(20)  # Update every 20 ms

    # Start the Qt event loop
    pg.QtWidgets.QApplication.instance().exec_()

    data_file.close()

if __name__ == "__main__":
    main()

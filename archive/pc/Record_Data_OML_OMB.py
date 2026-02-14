import socket
import sys
import os
import time
import ast
import threading
from queue import Queue

import numpy as np
from vispy import app, scene

# Constants for sample range
MAX_SAMP_LASK5 = 3096
MIN_SAMP_LASK5 = 2048

MAX_SAMP_SENSORBAND = 4096
MIN_SAMP_SENSORBAND = 2048

# Constants for data handling
MAX_DATA_POINTS = 500  # Adjusted buffer size

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
    """Main function to run the UDP server and VisPy visualization."""
    ip, port = parse_args()
    sock = create_udp_socket(ip, port)
    data_file = setup_data_file()

    packet_queue = Queue()

    receiver_thread = threading.Thread(target=packet_receiver, args=(sock, packet_queue))
    receiver_thread.daemon = True
    receiver_thread.start()

    # VisPy setup
    canvas = scene.SceneCanvas(keys='interactive', title='OpenMuscle Data Visualization', show=True)
    grid = canvas.central_widget.add_grid()

    # Initialize devices dictionary
    devices = {
        'OM-LASK5': {'plot': None, 'data': [np.zeros(MAX_DATA_POINTS) for _ in range(4)], 'lines': []},
        'SensorBand': {'plot': None, 'data': [np.zeros(MAX_DATA_POINTS) for _ in range(12)], 'lines': []}
    }

    # Colors for plotting
    colors = [
        '#FF0000',  # Red
        '#00FF00',  # Green
        '#0000FF',  # Blue
        '#FFFF00',  # Yellow
        '#FF00FF',  # Magenta
        '#00FFFF',  # Cyan
        '#800000',  # Maroon
        '#008000',  # Dark Green
        '#000080',  # Navy
        '#808000',  # Olive
        '#800080',  # Purple
        '#008080',  # Teal
    ]

    # Set up OM-LASK5 plot
    om_lask5_view = grid.add_view(row=0, col=0, camera='panzoom')
    om_lask5_view.camera.set_range(x=(0, MAX_DATA_POINTS), y=(MIN_SAMP_LASK5, MAX_SAMP_LASK5))
    devices['OM-LASK5']['plot'] = om_lask5_view
    num_channels = 4
    for i in range(num_channels):
        line = scene.Line(
            np.zeros((MAX_DATA_POINTS, 2)),
            color=colors[i % len(colors)],
            parent=om_lask5_view.scene
        )
        devices['OM-LASK5']['lines'].append(line)

    # Set up SensorBand plot
    sensor_band_view = grid.add_view(row=1, col=0, camera='panzoom')
    sensor_band_view.camera.set_range(x=(0, MAX_DATA_POINTS), y=(MIN_SAMP_SENSORBAND, MAX_SAMP_SENSORBAND))
    devices['SensorBand']['plot'] = sensor_band_view
    num_sensors = 12
    for i in range(num_sensors):
        line = scene.Line(
            np.zeros((MAX_DATA_POINTS, 2)),
            color=colors[i % len(colors)],
            parent=sensor_band_view.scene
        )
        devices['SensorBand']['lines'].append(line)

    t0 = time.time()

    # Timer for updating the plots
    timer = app.Timer()

    def update(event):
        """Update function called by the timer."""
        try:
            updated_devices = set()
            while not packet_queue.empty():
                packet = packet_queue.get()
                packet['rec_time'] = time.time() - t0
                data_file.write(str(packet) + '\n')
                device_id = packet['id']
                if device_id == 'OM-LASK5':
                    device = devices['OM-LASK5']
                    # Update data arrays
                    for i, value in enumerate(packet['data']):
                        device['data'][i] = np.roll(device['data'][i], -1)
                        device['data'][i][-1] = value
                    updated_devices.add('OM-LASK5')
                elif device_id.startswith('OM-SB-V1-C.'):
                    sensor_band = devices['SensorBand']
                    hall_index = packet.get('hallIndex', [])
                    data_values = packet.get('data', [])
                    for index, value in zip(hall_index, data_values):
                        if 0 <= index < 12:
                            sensor_band['data'][index] = np.roll(sensor_band['data'][index], -1)
                            sensor_band['data'][index][-1] = value
                    updated_devices.add('SensorBand')

            # Update plots only if data has changed
            if 'OM-LASK5' in updated_devices:
                device = devices['OM-LASK5']
                x = np.arange(MAX_DATA_POINTS)
                for i, line in enumerate(device['lines']):
                    y = device['data'][i]
                    points = np.column_stack((x, y))
                    line.set_data(points)

            if 'SensorBand' in updated_devices:
                sensor_band = devices['SensorBand']
                x = np.arange(MAX_DATA_POINTS)
                for i, line in enumerate(sensor_band['lines']):
                    y = sensor_band['data'][i]
                    points = np.column_stack((x, y))
                    line.set_data(points)
        except Exception as e:
            print(f"Error in update function: {e}")

    timer.connect(update)
    timer.start(0.05)  # Update every 50 ms

    app.run()

    data_file.close()

if __name__ == "__main__":
    main()

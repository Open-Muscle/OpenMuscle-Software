import socket
import sys
import os
import time
import ast
import threading
from queue import Queue
import numpy as np
from vispy import app, scene
import csv
import pickle  # For loading your ML model
from collections import deque
import signal  # For handling system signals
import pandas as pd  # Added import for pandas
import warnings  # For suppressing warnings

# Suppress specific warnings
warnings.filterwarnings('ignore', message='X does not have valid feature names')

# --- Data Parsing Classes (from your data parser script) ---

class DataParser:
    @staticmethod
    def parse_line(line):
        try:
            data = ast.literal_eval(line.strip())
            return data
        except Exception as e:
            print(f"Failed to parse line: {line}. Error: {e}")
            return None

class BufferManager:
    def __init__(self):
        self.buffers = {
            'OM-SB-V1-C.0': deque(maxlen=5),
            'OM-SB-V1-C.1': deque(maxlen=5),
            'OM-SB-V1-C.2': deque(maxlen=5),
            'OM-LASK5': deque(maxlen=5),
        }

    def add_packet(self, packet):
        packet_id = packet.get('id')
        if packet_id in self.buffers:
            self.buffers[packet_id].append(packet)
        else:
            pass  # Ignoring packets that are not of interest

class Matcher:
    def __init__(self, buffer_manager):
        self.buffer_manager = buffer_manager

    def match_packets(self):
        sensors = []
        sensor_timestamps = []
        # Check if we have at least one packet in each sensor buffer
        for sensor_id in ['OM-SB-V1-C.0', 'OM-SB-V1-C.1', 'OM-SB-V1-C.2']:
            if not self.buffer_manager.buffers[sensor_id]:
                return None  # Waiting for more sensor data
            else:
                sensors.append(self.buffer_manager.buffers[sensor_id][-1])
                sensor_timestamps.append(self.buffer_manager.buffers[sensor_id][-1]['rec_time'])

        # Check if we have at least one label packet
        if not self.buffer_manager.buffers['OM-LASK5']:
            return None  # Waiting for label data
        label_packet = self.buffer_manager.buffers['OM-LASK5'][-1]

        # Combine data
        combined_record = self.combine_data(sensors, label_packet)
        return combined_record

    def combine_data(self, sensors, label_packet):
        sensor_values = []
        for sensor in sensors:
            sensor_values.extend(sensor.get('data', []))

        # Get the earliest sensor timestamp
        sensor_timestamp = min(sensor['rec_time'] for sensor in sensors)
        labels = label_packet.get('data', [])
        label_timestamp = label_packet['rec_time']

        record = sensor_values + [sensor_timestamp] + labels + [label_timestamp]
        return record

class CSVWriter:
    def __init__(self, filename, directory):
        self.directory = directory
        self.filename = filename
        # Ensure the directory exists
        os.makedirs(self.directory, exist_ok=True)
        self.filepath = os.path.join(self.directory, self.filename)
        # Open the file once
        self.csvfile = open(self.filepath, 'w', newline='')
        self.writer = csv.writer(self.csvfile)
        # Write the header
        header = [f"Sensor_{i}" for i in range(12)] + ['Sensor_Timestamp'] + \
                 [f"Label_{i}" for i in range(4)] + ['Label_Timestamp']
        self.writer.writerow(header)

    def write_record(self, record):
        if len(record) != 18:
            print(f"Record does not have 18 values: {record}")
            return
        self.writer.writerow(record)

    def close(self):
        self.csvfile.close()

# --- End of Data Parsing Classes ---

# Constants for sample range
MAX_SAMP_LASK5 = 3096
MIN_SAMP_LASK5 = 2048

MAX_SAMP_SENSORBAND = 3500
MIN_SAMP_SENSORBAND = 2500

PREDICTION_OFFSET = 25  # Adjust this value as needed
# Constants for data handling
MAX_DATA_POINTS = 500  # Adjusted buffer size

labels_data = [np.zeros(MAX_DATA_POINTS) for _ in range(4)]
predictions_data = [np.zeros(MAX_DATA_POINTS) for _ in range(4)]

def get_local_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to an external host to get the local IP address
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    finally:
        s.close()
    return local_ip

def parse_args():
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
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((ip, port))
    print(f'Server Address: {ip} Port: {port}')
    print('Press Ctrl+C to exit the program!')
    return sock

def main():
    ip, port = parse_args()
    sock = create_udp_socket(ip, port)

    packet_queue = Queue()

    # Initialize the BufferManager, Matcher, and CSVWriter
    buffer_manager = BufferManager()
    matcher = Matcher(buffer_manager)
    csv_directory = 'Data-Captures'
    csv_writer = CSVWriter('filtered_output.csv', csv_directory)

    # Load your machine learning model
    #model_filename = 'random_forest_regressor.pkl'
    model_filename = 'random_forest_regressor_13_features.pkl'
    try:
        model = pickle.load(open(model_filename, 'rb'))
        print(f"Model loaded from {model_filename}")
    except Exception as e:
        print(f"Failed to load model: {e}")
        model = None  # Proceed without predictions if model fails to load

    def packet_receiver(sock, packet_queue):
        while True:
            try:
                data, address = sock.recvfrom(4096)
                text = data.decode('utf-8')
                packet = ast.literal_eval(text)
                packet['rec_time'] = time.time()  # Add the receive timestamp
                packet_queue.put(packet)
            except socket.error:
                pass  # No data received
            except Exception as e:
                print(f"Error parsing packet: {e}")
                print(f"Data was: {text}")

    receiver_thread = threading.Thread(target=packet_receiver, args=(sock, packet_queue))
    receiver_thread.daemon = True
    receiver_thread.start()

    # VisPy setup
    canvas = scene.SceneCanvas(keys='interactive', title='OpenMuscle Data Visualization', show=True)
    grid = canvas.central_widget.add_grid()

    devices = {
        'OM-LASK5': {'plot': None, 'data': [np.zeros(MAX_DATA_POINTS) for _ in range(4)], 'lines': []},
        'SensorBand': {'plot': None, 'data': [np.zeros(MAX_DATA_POINTS) for _ in range(12)], 'lines': []}
    }

    # Colors for plotting OM-LASK5 and SensorBand
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

    # Initialize data arrays for labels and predictions
    labels_data = [np.zeros(MAX_DATA_POINTS) for _ in range(4)]
    predictions_data = [np.zeros(MAX_DATA_POINTS) for _ in range(4)]

    # Set up plot for labels and predictions
    labels_view = grid.add_view(row=2, col=0, camera='panzoom')
    labels_view.camera.set_range(x=(0, MAX_DATA_POINTS), y=(0, 180))  # Adjust y-range as needed

    # Define colors for labels and predictions
    label_colors = ['#FF0000', '#00FF00', '#0000FF', '#FFFF00']  # Magenta, Cyan, Yellow, Blue
    prediction_colors = ['#990000', '#009900', '#000099', '#999900']  # LightPink, LightSeaGreen, Gold, IndianRed

    labels_lines = []
    predictions_lines = []
    for i in range(4):
        print(f"Setting label line {i} color to {label_colors[i]}")
        # Plot true labels
        line_label = scene.Line(
            np.zeros((MAX_DATA_POINTS, 2)),
            color=label_colors[i],
            parent=labels_view.scene
        )
        labels_lines.append(line_label)
        # Plot predictions
        print(f"Setting prediction line {i} color to {prediction_colors[i]}")
        line_pred = scene.Line(
            np.zeros((MAX_DATA_POINTS, 2)),
            color=prediction_colors[i],
            parent=labels_view.scene,
            width=2  # Increase line width for visibility
        )
        predictions_lines.append(line_pred)

    t0 = time.time()

    # Timer for updating the plots
    timer = app.Timer()

    def update(event):
        try:
            updated_devices = set()
            while not packet_queue.empty():
                packet = packet_queue.get()
                device_id = packet['id']

                # Add packet to buffer manager for matching and CSV writing
                buffer_manager.add_packet(packet)
                record = matcher.match_packets()
                if record:
                    csv_writer.write_record(record)
                    # Prepare data for prediction
                    feature_names = [f"Sensor_{i}" for i in range(12)] + ['Sensor_Timestamp']
                    features = pd.DataFrame([record[:13]], columns=feature_names)  # Adjusted slice to include 13 features
                    true_labels = np.array(record[13:17])

                    if model:
                        prediction = model.predict(features)
                        prediction = prediction.flatten()
                        # Update labels and predictions data for plotting
                        for i in range(4):
                            # Update label data
                            labels_data[i] = np.roll(labels_data[i], -1)
                            labels_data[i][-1] = true_labels[i]
                            # Update prediction data
                            predictions_data[i] = np.roll(predictions_data[i], -1)
                            predictions_data[i][-1] = prediction[i]
                    else:
                        # If model not loaded, only update labels
                        for i in range(4):
                            labels_data[i] = np.roll(labels_data[i], -1)
                            labels_data[i][-1] = true_labels[i]

                # Update visualization data
                if device_id == 'OM-LASK5':
                    device = devices['OM-LASK5']
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

            # Update label and prediction plots
            x = np.arange(MAX_DATA_POINTS)
            for i in range(4):
                # Update labels
                y_labels = labels_data[i]
                points_labels = np.column_stack((x, y_labels))
                labels_lines[i].set_data(points_labels)
                # Update predictions if model is loaded
                if model:
                    y_preds = predictions_data[i]
                    points_preds = np.column_stack((x, y_preds))
                    predictions_lines[i].set_data(points_preds)

            # Adjust y-axis range if necessary
            y_min = min(np.min(labels_data), np.min(predictions_data)) - 10
            y_max = max(np.max(labels_data), np.max(predictions_data)) + 10
            labels_view.camera.set_range(x=(0, MAX_DATA_POINTS), y=(y_min, y_max))

        except Exception as e:
            print(f"Error in update function: {e}")

    timer.connect(update)
    timer.start(0.05)  # Update every 50 ms

    app.run()


if __name__ == "__main__":
    main()

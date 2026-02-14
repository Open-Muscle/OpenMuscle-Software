# OM_DataParser.py Designed for Open Muscle Data Captures
# 11/24/2024

import os
import csv
import time
import threading
import ast
from collections import deque

class DataParser:
    """
    Parses lines of data into Python dictionaries.
    Handles the conversion from string representations to actual data structures.
    """

    @staticmethod
    def parse_line(line):
        """
        Parses a line of data into a Python dictionary.
        Uses ast.literal_eval for safe parsing.
        """
        try:
            data = ast.literal_eval(line.strip())
            return data
        except Exception as e:
            print(f"Failed to parse line: {line}. Error: {e}")
            return None

class BufferManager:
    """
    Manages rolling buffers for each packet type.
    Maintains the most recent 20 packets for each type.
    """

    def __init__(self):
        self.buffers = {
            'OM-SB-V1-C.0': deque(maxlen=5),
            'OM-SB-V1-C.1': deque(maxlen=5),
            'OM-SB-V1-C.2': deque(maxlen=5),
            'OM-LASK5': deque(maxlen=5),
        }

    def add_packet(self, packet):
        """
        Adds a packet to the appropriate buffer based on its 'id'.
        """
        packet_id = packet.get('id')
        if packet_id in self.buffers:
            self.buffers[packet_id].append(packet)
        else:
            # Ignoring packets that are not of interest
            pass

class Matcher:
    """
    Matches packets from the buffers based on the closest timestamps.
    Combines sensor data and label data into a single record.
    """

    def __init__(self, buffer_manager):
        self.buffer_manager = buffer_manager

    def match_packets(self):
        """
        Attempts to match the latest packets from each buffer.
        Returns a combined record if successful, else None.
        """
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

        # Match based on closest timestamps
        # For simplicity, we take the latest packets assuming they are close enough
        # In a real scenario, you might need to implement more sophisticated matching
        combined_record = self.combine_data(sensors, label_packet)
        return combined_record

    def combine_data(self, sensors, label_packet):
        """
        Combines data from sensor packets and label packet into a single record.
        """
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
    """
    Writes matched data into a CSV file.
    Ensures that each row contains exactly 18 values.
    """

    def __init__(self, filename):
        self.filename = filename
        # Write the header
        with open(self.filename, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            header = [f"Sensor_{i}" for i in range(12)] + ['Sensor_Timestamp'] + \
                     [f"Label_{i}" for i in range(4)] + ['Label_Timestamp']
            writer.writerow(header)

    def write_record(self, record):
        """
        Writes a single record to the CSV file.
        """
        if len(record) != 18:
            print(f"Record does not have 18 values: {record}")
            return
        with open(self.filename, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(record)

def process_file_mode(file_path, csv_writer):
    """
    Processes data from a file all at once.
    """
    buffer_manager = BufferManager()
    matcher = Matcher(buffer_manager)

    with open(file_path, 'r') as f:
        for line in f:
            packet = DataParser.parse_line(line)
            if packet:
                buffer_manager.add_packet(packet)
                record = matcher.match_packets()
                if record:
                    csv_writer.write_record(record)

def process_real_time_mode(file_path, csv_writer):
    """
    Simulates real-time data arrival by reading from a file with delays.
    """
    buffer_manager = BufferManager()
    matcher = Matcher(buffer_manager)

    def read_data():
        with open(file_path, 'r') as f:
            for line in f:
                packet = DataParser.parse_line(line)
                if packet:
                    buffer_manager.add_packet(packet)
                    # Simulate data arrival delay
                    time.sleep(0.01)

    # Start the data reading in a separate thread
    reader_thread = threading.Thread(target=read_data)
    reader_thread.start()

    # Process data in the main thread
    while reader_thread.is_alive() or any(buffer_manager.buffers.values()):
        record = matcher.match_packets()
        if record:
            csv_writer.write_record(record)
        else:
            time.sleep(0.01)  # Avoid busy waiting

def main():
    # Determine the mode
    mode = input("Enter mode ('file' or 'real-time'): ").strip().lower()
    captures_folder = os.path.join(os.getcwd(), 'Captures')
    if not os.path.exists(captures_folder):
        print(f"Captures folder not found at {captures_folder}")
        return

    # Assume there's only one file in the Captures folder for simplicity
    files = os.listdir(captures_folder)
    if not files:
        print("No files found in the Captures folder.")
        return
    file_path = os.path.join(captures_folder, files[0])

    csv_writer = CSVWriter(f'filtered_output_{files[0]}.csv')

    if mode == 'file':
        process_file_mode(file_path, csv_writer)
    elif mode == 'real-time':
        process_real_time_mode(file_path, csv_writer)
    else:
        print("Invalid mode selected.")

if __name__ == '__main__':
    main()

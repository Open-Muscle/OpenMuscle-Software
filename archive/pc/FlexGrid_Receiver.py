import socket
import os
import time
import ast
import threading
import csv
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from queue import Queue

# Constants
UDP_IP = "0.0.0.0"
UDP_PORT = 3141
MATRIX_ROWS = 4  # Visualize as 4 rows x 16 columns (adjusted based on received packet shape)
MATRIX_COLS = 16
SAVE_DIR = "Data-Captures"
os.makedirs(SAVE_DIR, exist_ok=True)
FILENAME = os.path.join(SAVE_DIR, f"flexgrid_capture_{int(time.time())}.csv")
packet_count = 0
last_print_time = time.time()

# Initialize matrix
pressure_matrix = np.zeros((MATRIX_ROWS, MATRIX_COLS))
packet_queue = Queue()

# Set up CSV logging
csv_file = open(FILENAME, "w", newline="")
csv_writer = csv.writer(csv_file)
header = [f"R{r}C{c}" for r in range(MATRIX_ROWS) for c in range(MATRIX_COLS)]
header.insert(0, "timestamp")
csv_writer.writerow(header)

# UDP packet reception
def receive_udp_data():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    print(f"Listening on {UDP_IP}:{UDP_PORT}...")
    while True:
        try:
            data, _ = sock.recvfrom(8192)
            text = data.decode("utf-8")
            packet = ast.literal_eval(text)
            packet_queue.put(packet)
        except Exception as e:
            print(f"Error receiving/parsing packet: {e}")

# Real-time plot update
def update_plot(frame):
    global pressure_matrix, packet_count, last_print_time

    updated = False
    while not packet_queue.empty():
        matrix_data = packet_queue.get()
        # Check for nested list: 16 columns x 4 rows, transpose to 4x16 for visualization
        if isinstance(matrix_data, list) and len(matrix_data) == MATRIX_COLS and all(isinstance(col, list) and len(col) == MATRIX_ROWS for col in matrix_data):
            pressure_matrix[:] = np.array(matrix_data).T  # Transpose to (4, 16)
            # Flatten the transposed matrix for CSV (row-major order)
            matrix_data_flat = pressure_matrix.flatten().tolist()
            row = [time.time()] + matrix_data_flat
            csv_writer.writerow(row)
            packet_count += 1
            updated = True
        else:
            print(f"Invalid packet shape: expected {MATRIX_COLS}x{MATRIX_ROWS}, got {len(matrix_data) if isinstance(matrix_data, list) else 'non-list'}")

    # Print packet count to terminal every second
    current_time = time.time()
    if current_time - last_print_time > 1:
        print(f"Packets received: {packet_count}")
        last_print_time = current_time

    # Only update display if we got a valid packet
    if updated:
        im.set_array(pressure_matrix)
        ax.set_title(f"FlexGrid Pressure Sensor Matrix — Packets: {packet_count}")
        return [im]
    else:
        return []

# Start background receiver thread
receiver_thread = threading.Thread(target=receive_udp_data, daemon=True)
receiver_thread.start()

# Matplotlib setup
fig, ax = plt.subplots()
im = ax.imshow(pressure_matrix, cmap="plasma", vmin=0, vmax=4096)  # Adjusted vmin for typical sensor values (0-4095)
ax.set_title("FlexGrid Pressure Sensor Matrix")

# shrink the colorbar to 50% of the axes height
cbar = plt.colorbar(im, ax=ax, shrink=0.5, aspect=10)
cbar.set_label("Pressure value")  # optional label

# Start animation loop
ani = FuncAnimation(fig, update_plot, interval=50, cache_frame_data=False)  # Disable cache to avoid warning

try:
    plt.show()
finally:
    csv_file.close()
    print(f"CSV saved to {FILENAME}")

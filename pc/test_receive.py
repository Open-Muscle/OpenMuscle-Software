import socket
import json
import csv
import os
import time


# UDP listen configuration
UDP_IP = "0.0.0.0"
UDP_PORT = 3141

# CSV setup
SAVE_DIR = "udp_captures"
os.makedirs(SAVE_DIR, exist_ok=True)
filename = os.path.join(SAVE_DIR, f"packet_log_{int(time.time())}.csv")
csv_file = open(filename, "w", newline="")
csv_writer = csv.writer(csv_file)

# Set up UDP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
print(f"Listening for UDP packets on {UDP_IP}:{UDP_PORT}...")

try:
    while True:
        data, addr = sock.recvfrom(8192)
        try:
            decoded = json.loads(data.decode("utf-8"))
            if isinstance(decoded, list) and len(decoded) == 60:
                print(f"Received from {addr}: First 5 values: {decoded[:5]} ...")
                csv_writer.writerow([time.time()] + decoded)
            else:
                print(f"Received unknown data structure from {addr}: {decoded}")
        except Exception as e:
            print(f"Failed to decode packet from {addr}: {e}")
except KeyboardInterrupt:
    print("\nStopped by user.")
finally:
    csv_file.close()
    sock.close()

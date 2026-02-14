"""Virtual sensor transmitter for testing without hardware.

Can generate synthetic data or replay a capture file.
"""

import json
import socket
import time
import random


def run_simulator(port: int = 3141, device_type: str = "flexgrid",
                  replay_file: str = None, target_ip: str = "127.0.0.1"):
    """Send synthetic or replayed sensor data over UDP.

    Args:
        port: UDP port to send to
        device_type: device type to simulate ("flexgrid" or "lask5")
        replay_file: path to a capture .txt file to replay (optional)
        target_ip: IP address to send to
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    if replay_file:
        _replay(sock, replay_file, target_ip, port)
    else:
        _generate(sock, device_type, target_ip, port)


def _generate(sock, device_type: str, ip: str, port: int):
    """Generate and send synthetic packets."""
    print(f"Generating synthetic {device_type} data -> {ip}:{port}")
    print("Press Ctrl+C to stop")

    pkt_num = 0
    try:
        while True:
            if device_type == "flexgrid":
                matrix = [[random.randint(0, 4095) for _ in range(4)] for _ in range(16)]
                pkt = {
                    "v": "1.0",
                    "type": "flexgrid",
                    "id": "flexgrid-sim",
                    "ts": int(time.time() * 1000) % 2**31,
                    "data": {"matrix": matrix, "rows": 4, "cols": 16},
                }
                interval = 0.1
            elif device_type == "lask5":
                values = [random.randint(0, 2500) for _ in range(4)]
                pkt = {
                    "v": "1.0",
                    "type": "lask5",
                    "id": "lask5-sim",
                    "ts": int(time.time() * 1000) % 2**31,
                    "data": {"values": values},
                }
                interval = 0.04  # 25 Hz
            else:
                print(f"Unknown device type: {device_type}")
                return

            raw = json.dumps(pkt).encode("utf-8")
            sock.sendto(raw, (ip, port))
            pkt_num += 1

            if pkt_num % 100 == 0:
                print(f"Sent {pkt_num} packets")

            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\nStopped after {pkt_num} packets")


def _replay(sock, filepath: str, ip: str, port: int):
    """Replay a capture file by re-sending each line as a UDP packet."""
    import ast

    print(f"Replaying {filepath} -> {ip}:{port}")
    pkt_num = 0

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # Try to send the raw line (preserves original format)
            sock.sendto(line.encode("utf-8"), (ip, port))
            pkt_num += 1

            if pkt_num % 100 == 0:
                print(f"Replayed {pkt_num} packets")

            time.sleep(0.001)

    print(f"Replay complete: {pkt_num} packets sent")

"""Virtual sensor transmitter for testing without hardware.

Can generate synthetic data or replay a capture file.
"""

import json
import socket
import time
import random


def run_simulator(port: int = 3141, device_type: str = "flexgrid",
                  replay_file: str = None, target_ip: str = "127.0.0.1",
                  web_port: int = 8000):
    """Send synthetic or replayed sensor data.

    Args:
        port: UDP port to send to
        device_type: device type to simulate ("flexgrid", "lask5",
            "quest_hand", or "combo"). quest_hand streams synthetic WebXR
            hand frames to the web server's /ws/quest WebSocket (no UDP).
            combo streams a flexgrid UDP device AND a quest hand driven by
            the same latent finger curls, so a recorded capture is
            actually learnable end to end without hardware.
        replay_file: path to a capture .txt file to replay (optional)
        target_ip: IP address to send to
        web_port: HTTP port of the running `openmuscle web` server
            (quest_hand/combo only)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    if replay_file:
        _replay(sock, replay_file, target_ip, port)
    elif device_type in ("quest_hand", "combo"):
        _generate_quest(sock, device_type, target_ip, port, web_port)
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


def _generate_quest(sock, device_type: str, ip: str, udp_port: int,
                    web_port: int):
    """Stream synthetic WebXR hand frames to /ws/quest, and for combo mode
    a correlated flexgrid device over UDP alongside.

    The hand goes over WebSocket because that's the only transport the
    real Quest client has; reusing the same ingest path means everything
    downstream (recorder, matcher, snapshot, both hand viewers) sees the
    simulator exactly as it would see a headset.
    """
    import random
    from websockets.sync.client import connect
    from openmuscle.simulate.quest_hand import finger_curls, hand_frame, \
        flexgrid_matrix

    url = f"ws://{ip}:{web_port}/ws/quest"
    print(f"Streaming synthetic quest_hand frames -> {url}")
    if device_type == "combo":
        print(f"  + correlated flexgrid UDP -> {ip}:{udp_port}")
    print("Press Ctrl+C to stop (reconnects if the server restarts)")

    rng = random.Random(1138)
    frame_num = 0
    t0 = time.time()
    try:
        while True:
            try:
                with connect(url) as ws:
                    while True:
                        t = time.time() - t0
                        ws.send(json.dumps(hand_frame(t)))
                        frame_num += 1
                        # Flexgrid at half the hand rate (~12.5 Hz vs 25 Hz),
                        # like the real rig where sensor and label rates differ.
                        if device_type == "combo" and frame_num % 2 == 0:
                            pkt = {
                                "v": "1.0",
                                "type": "flexgrid",
                                "id": "flexgrid-sim",
                                "ts": int(time.time() * 1000) % 2**31,
                                "data": {
                                    "matrix": flexgrid_matrix(finger_curls(t), rng),
                                    "rows": 4, "cols": 16,
                                },
                            }
                            sock.sendto(json.dumps(pkt).encode("utf-8"),
                                        (ip, udp_port))
                        if frame_num % 250 == 0:
                            print(f"Sent {frame_num} hand frames")
                        time.sleep(0.04)   # 25 Hz
            except Exception as e:
                # KeyboardInterrupt is a BaseException, so Ctrl+C still
                # reaches the outer handler.
                print(f"WebSocket dropped ({type(e).__name__}: {e}); "
                      f"retrying in 2s")
                time.sleep(2)
    except KeyboardInterrupt:
        print(f"\nStopped after {frame_num} hand frames")


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

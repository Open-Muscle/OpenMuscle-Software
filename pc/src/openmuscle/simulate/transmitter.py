"""Virtual sensor transmitter for testing without hardware.

Can generate synthetic data or replay a capture file.
"""

import json
import socket
import time
import random


def run_simulator(port: int = 3141, device_type: str = "flexgrid",
                  replay_file: str = None, target_ip: str = "127.0.0.1",
                  web_port: int = 8000, web_scheme: str = "auto",
                  announce_port: int = 3140, bands: int = 2,
                  labeler: str = "lask5", noise="default", seed: int = 1138,
                  speed: float = 1.0, duration: float = None,
                  tag_roles: bool = True):
    """Send synthetic or replayed sensor data.

    Args:
        port: UDP port to send to
        device_type: device type to simulate ("flexgrid", "lask5",
            "quest_hand", "combo" or "rehearsal"). quest_hand streams
            synthetic WebXR hand frames to the web server's /ws/quest
            WebSocket (no UDP). combo streams a flexgrid UDP device AND a
            quest hand driven by the same latent finger curls, so a recorded
            capture is actually learnable end to end without hardware.
            rehearsal is a whole V4 kit (see simulate/rehearsal.py): one or two
            15x4 bands that announce on the discovery port plus a finger
            labeler, all driven by the same curls. flexgrid and lask5 send
            random noise: they exercise the plumbing but are not learnable.
        replay_file: path to a capture .txt file to replay (optional)
        target_ip: IP address to send to
        web_port: HTTP(S) port of the running `openmuscle web` server
            (quest_hand/combo, and webcam/quest labels in rehearsal)
        web_scheme: "auto" (probe the port for TLS), "http" or "https". The
            WebSocket follows it (ws:// or wss://).
        duration: stop after this many seconds (rehearsal, quest_hand, combo)
        announce_port, bands, labeler, noise, seed, speed, tag_roles:
            rehearsal only (see RehearsalConfig / RehearsalSimulator)
    """
    if device_type == "rehearsal":
        from openmuscle.simulate.rehearsal import (
            RehearsalConfig, RehearsalSimulator)
        sim = RehearsalSimulator(
            RehearsalConfig(bands=bands, labeler=labeler, noise=noise, seed=seed),
            target_ip=target_ip, udp_port=port, announce_port=announce_port,
            web_port=web_port, web_scheme=web_scheme, speed=speed,
            tag_roles=tag_roles)
        sim.run_forever(duration=duration)
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    if replay_file:
        # Reconstruct FlexGrid (UDP) + Quest (/ws/quest) frames from the capture
        # so a recorded two-hand session re-runs the whole pipeline. The web
        # server must be up for the Quest WebSocket leg.
        from openmuscle.simulate.replay import replay_capture
        print(f"Replaying {replay_file} -> UDP {target_ip}:{port}, "
              f"quest -> ws://{target_ip}:{web_port}/ws/quest")
        replay_capture(replay_file, target_ip=target_ip, udp_port=port,
                       web_port=web_port, speed=1.0, web_scheme=web_scheme)
    elif device_type in ("quest_hand", "combo"):
        _generate_quest(sock, device_type, target_ip, port, web_port,
                        web_scheme, duration=duration)
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
                    web_port: int, web_scheme: str = "auto",
                    stop_event=None, duration: float = None):
    """Stream synthetic WebXR hand frames to /ws/quest, and for combo mode
    a correlated flexgrid device over UDP alongside.

    The hand goes over WebSocket because that's the only transport the
    real Quest client has; reusing the same ingest path means everything
    downstream (recorder, matcher, snapshot, both hand viewers) sees the
    simulator exactly as it would see a headset.

    stop_event (a threading.Event) and duration (seconds) are optional ways to
    end the stream without Ctrl+C; both are used by tests.
    """
    import random
    from openmuscle.simulate import net
    from openmuscle.simulate.quest_hand import finger_curls, hand_frame, \
        flexgrid_matrix

    scheme = net.resolve_scheme(web_scheme, ip, web_port)
    url = net.ws_url(scheme, ip, web_port, "/ws/quest")
    print(f"Streaming synthetic quest_hand frames -> {url}")
    if device_type == "combo":
        print(f"  + correlated flexgrid UDP -> {ip}:{udp_port}")
    print("Press Ctrl+C to stop (reconnects if the server restarts)")

    rng = random.Random(1138)
    frame_num = 0
    t0 = time.time()

    def done():
        return ((stop_event is not None and stop_event.is_set())
                or (duration is not None and time.time() - t0 >= duration))

    try:
        while not done():
            try:
                # Re-resolve each pass so a server restarted with or without
                # TLS is followed.
                scheme = net.resolve_scheme(web_scheme, ip, web_port)
                url = net.ws_url(scheme, ip, web_port, "/ws/quest")
                with net.ws_connect(scheme, ip, web_port, "/ws/quest") as ws:
                    while not done():
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
                print(f"WebSocket to {url} dropped ({type(e).__name__}: {e}); "
                      f"retrying in 2s. Is `openmuscle web` running on port "
                      f"{web_port}?")
                if stop_event is not None:
                    stop_event.wait(2)
                else:
                    time.sleep(2)
    except KeyboardInterrupt:
        print(f"\nStopped after {frame_num} hand frames")


# Capture replay moved to simulate/replay.py (CSV-aware: reconstructs per-band
# FlexGrid UDP + per-hand Quest /ws/quest frames, timestamp-paced). The old
# raw-line UDP pipe could not replay a recorded CSV or a two-hand session.

"""Bridge subscriber: keeps the PC subscribed to V4-protocol sources.

The PC app (`openmuscle web`) does not yet implement the V4 discovery +
subscribe protocol natively (that work lives in the OpenMuscle-Lab-Discovery
workspace). This helper bridges the gap: for each device IP you pass on the
command line, it opens a TCP command channel, sends `subscribe` with the PC's
own address, and sends a `heartbeat` once per second. Devices then unicast
sensor / label frames to the PC's UDP 3141 listener where openmuscle web
picks them up.

This script does NOT listen on UDP 3141 itself, so it never conflicts with
openmuscle web for the port. Both can run side by side.

Usage:
    python bridge_subscriber.py 10.0.0.23
    python bridge_subscriber.py 10.0.0.23 10.0.0.232 10.0.0.192:8002

Default cmd ports: 8001 for FlexGrid, 8002 for LASK5. Append `:<port>` to a
target to override. Auto-detect of device type happens by trying 8001 first
then 8002.

Press Ctrl-C to unsubscribe cleanly and exit.
"""

import argparse
import json
import socket
import sys
import threading
import time


PROTO_PORT = 3141      # UDP port openmuscle web listens on for sensor frames
HEARTBEAT_S = 1.0
HUB_ID = "bridge-subscriber"


def get_default_pc_ip(test_target=("10.0.0.1", 80)):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(test_target)
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def try_subscribe(ip, port, pc_ip):
    """Open TCP, send subscribe, return (tcp_socket, ack_dict) on success or
    (None, None) on failure."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect((ip, port))
        msg = json.dumps({
            "v": "1.0", "type": "cmd", "msg_id": 1,
            "data": {
                "verb": "subscribe",
                "host": pc_ip, "port": PROTO_PORT,
                "transport": "wifi", "hub_id": HUB_ID,
            },
        }) + "\n"
        s.send(msg.encode())
        ack = s.recv(2048).decode().strip()
        return s, json.loads(ack)
    except Exception:
        try:
            s.close()
        except Exception:
            pass
        return None, None


class Keeper:
    """One subscription + heartbeat keeper running on its own daemon thread."""

    def __init__(self, ip, port, pc_ip):
        self.ip = ip
        self.port = port
        self.pc_ip = pc_ip
        self.tcp = None
        self.alive = True
        self.thread = None

    def start(self):
        self.tcp, ack = try_subscribe(self.ip, self.port, self.pc_ip)
        if not self.tcp:
            return False
        verb_data = (ack or {}).get("data") or {}
        accepted = verb_data.get("accepted")
        print("  [{}:{}] subscribe ack: accepted={}, count={}/{}".format(
            self.ip, self.port, accepted,
            verb_data.get("subscriber_count"),
            verb_data.get("max_subscribers"),
        ))
        if not accepted:
            print("  (device rejected subscribe; subscriber list may be full)")
            self.tcp.close()
            return False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return True

    def _loop(self):
        msg_id = 100
        while self.alive:
            time.sleep(HEARTBEAT_S)
            if not self.alive:
                break
            msg = json.dumps({
                "v": "1.0", "type": "cmd", "msg_id": msg_id,
                "data": {
                    "verb": "heartbeat",
                    "host": self.pc_ip, "port": PROTO_PORT,
                    "transport": "wifi",
                },
            }) + "\n"
            try:
                self.tcp.send(msg.encode())
                self.tcp.recv(2048)
                msg_id += 1
            except Exception as e:
                print("  [{}:{}] heartbeat failed: {}; restarting subscribe".format(
                    self.ip, self.port, e))
                try:
                    self.tcp.close()
                except Exception:
                    pass
                # Try to re-subscribe
                self.tcp, _ack = try_subscribe(self.ip, self.port, self.pc_ip)
                if not self.tcp:
                    print("  [{}:{}] re-subscribe failed; giving up".format(self.ip, self.port))
                    self.alive = False
                    break
                msg_id = 100

    def stop(self):
        self.alive = False
        if not self.tcp:
            return
        try:
            self.tcp.send((json.dumps({
                "v": "1.0", "type": "cmd", "msg_id": 999,
                "data": {"verb": "unsubscribe", "host": self.pc_ip, "port": PROTO_PORT},
            }) + "\n").encode())
            self.tcp.recv(2048)
        except Exception:
            pass
        try:
            self.tcp.close()
        except Exception:
            pass


def parse_target(target_str):
    """`'1.2.3.4'` -> [(1.2.3.4, 8001), (1.2.3.4, 8002)] for autodetect.
       `'1.2.3.4:8002'` -> [(1.2.3.4, 8002)] exact."""
    if ":" in target_str:
        ip, port = target_str.split(":", 1)
        return [(ip, int(port))]
    return [(target_str, 8001), (target_str, 8002)]


def main():
    p = argparse.ArgumentParser(description="Keep PC subscribed to V4 sources")
    p.add_argument("targets", nargs="+", help="Device IPs (default ports 8001/8002 tried)")
    p.add_argument("--pc-ip", default=None, help="Override PC's own LAN IP")
    args = p.parse_args()

    pc_ip = args.pc_ip or get_default_pc_ip()
    print("PC IP: {}  (sensor/label frames will arrive at this address, UDP {})".format(
        pc_ip, PROTO_PORT))

    keepers = []
    for t in args.targets:
        for ip, port in parse_target(t):
            print("trying {}:{}".format(ip, port))
            k = Keeper(ip, port, pc_ip)
            if k.start():
                keepers.append(k)
                break
        else:
            print("  no responsive cmd server at {}".format(t))

    if not keepers:
        print("\nNo subscriptions established. Check device IPs + cmd ports.")
        sys.exit(1)

    print("\n{} subscription(s) active. Heartbeating every {}s.".format(len(keepers), HEARTBEAT_S))
    print("Open http://localhost:8000 (openmuscle web) to see the live data.")
    print("Press Ctrl-C to unsubscribe + exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nshutting down. unsubscribing...")
        for k in keepers:
            k.stop()
        print("done.")


if __name__ == "__main__":
    main()

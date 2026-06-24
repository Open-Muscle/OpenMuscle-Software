"""Threaded UDP listener that parses incoming packets into a queue."""

import socket
import threading
from queue import Queue

from openmuscle.protocol.parser import parse_packet


class UDPListener:
    """Single-threaded UDP listener that puts parsed packets onto a queue.

    Usage:
        listener = UDPListener(port=3141)
        listener.start()
        while True:
            pkt = listener.packet_queue.get()
            # process pkt
    """

    def __init__(self, port: int = 3141, bind_ip: str = "0.0.0.0",
                 announce_handler=None):
        self.port = port
        self.bind_ip = bind_ip
        self.packet_queue: Queue = Queue()
        # Optional callback(announce_dict, src_ip) for V4 discovery beacons.
        # When set, announce packets are routed here and NOT enqueued as sensor
        # frames. When None (CLI inference/heatmap tools), behavior is unchanged.
        self.announce_handler = announce_handler
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _listen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)
        sock.bind((self.bind_ip, self.port))
        print(f"Listening on {self.bind_ip}:{self.port}")

        while self._running:
            try:
                data, addr = sock.recvfrom(8192)
                pkt = parse_packet(data)
                if pkt is None:
                    continue
                # Divert V4 discovery beacons to the discovery handler; the
                # announce carries no IP, so pass the datagram source address.
                if pkt.device_type == "announce":
                    if self.announce_handler is not None:
                        try:
                            self.announce_handler(pkt.data, addr[0])
                        except Exception as e:
                            print(f"announce handler error: {e}")
                    continue
                self.packet_queue.put(pkt)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Receiver error: {e}")
        sock.close()

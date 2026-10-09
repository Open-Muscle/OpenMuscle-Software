"""Threaded UDP listener that parses incoming packets into a queue."""

import logging
import socket
import threading
import time
from queue import Queue

from openmuscle import netutil
from openmuscle.protocol.parser import parse_packet

logger = logging.getLogger(__name__)


class ListenerBindError(OSError):
    """The data port could not be opened. str(e) is one plain sentence."""


class UDPListener:
    """Single-threaded UDP listener that puts parsed packets onto a queue.

    Usage:
        listener = UDPListener(port=3141)
        listener.start()
        while True:
            pkt = listener.packet_queue.get()
            # process pkt

    The port is bound exclusively (a second OpenMuscle window, or any other
    program, cannot share it and split the traffic). The bind happens inside
    start(), so a busy port is known immediately: bound is False and bind_error
    holds a plain sentence for the UI and logs. With strict=True start() raises
    ListenerBindError instead (command-line tools that cannot do anything
    without the port).
    """

    def __init__(self, port: int = 3141, bind_ip: str = "0.0.0.0",
                 announce_handler=None, rcvbuf: int = 4 * 1024 * 1024,
                 quiet: bool = False, strict: bool = False):
        self.port = port
        self.bind_ip = bind_ip
        # Requested UDP receive-buffer size. The default OS buffer (~64-256 KB on
        # Windows) overflows and silently drops datagrams when the device streams
        # at higher rates and the consumer stalls briefly. 4 MB absorbs the bursts; the
        # OS may clamp to its max (raise the registry/sysctl limit for more).
        self.rcvbuf = rcvbuf
        # quiet: skip the "Listening on ..." startup line (the web server prints
        # its own plain-language banner instead).
        self.quiet = quiet
        self.strict = strict
        self.packet_queue: Queue = Queue()
        # Optional callback(announce_dict, src_ip) for V4 discovery beacons.
        # When set, announce packets are routed here and NOT enqueued as sensor
        # frames. When None (CLI inference/heatmap tools), behavior is unchanged.
        self.announce_handler = announce_handler
        self._running = False
        self._thread = None
        self._sock = None
        # Health: None until start() has tried to bind, then True / False.
        self.bound = None
        self.bind_error = ""
        self.bound_port = None
        self.packets_total = 0
        self.last_packet_time = 0.0
        self._rate = netutil.RateWindow()

    def start(self):
        self._running = True
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        netutil.make_exclusive(sock)
        # Enlarge the receive buffer to avoid dropping frames at rate.
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.rcvbuf)
        except OSError:
            pass
        granted = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        sock.settimeout(1.0)
        try:
            sock.bind((self.bind_ip, self.port))
        except OSError as e:
            sock.close()
            self._running = False
            self.bound = False
            self.bind_error = netutil.bind_error_text(self.port, "UDP", e)
            logger.error("receiver: %s Sensor data cannot arrive until it is freed.",
                         self.bind_error)
            if self.strict:
                raise ListenerBindError(self.bind_error) from e
            return
        self._sock = sock
        self.bound = True
        self.bind_error = ""
        self.bound_port = sock.getsockname()[1]
        if not self.quiet:
            print(f"Listening on {self.bind_ip}:{self.bound_port} (rcvbuf={granted})")
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def health(self, now=None):
        """Plain-data view for the UI and `openmuscle doctor`."""
        now = now if now is not None else time.time()
        return {
            "port": self.port,
            "bound_host": self.bind_ip,
            "ok": self.bound,
            "error": self.bind_error,
            "packets_total": self.packets_total,
            "packets_per_min": self._rate.per_minute(now),
            "last_packet_age_s": (round(now - self.last_packet_time, 2)
                                  if self.last_packet_time else None),
        }

    def _listen(self):
        sock = self._sock
        while self._running:
            try:
                data, addr = sock.recvfrom(8192)
                self.packets_total += 1
                self.last_packet_time = time.time()
                self._rate.add()
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
                if not self._running:
                    break
                print(f"Receiver error: {e}")
        sock.close()

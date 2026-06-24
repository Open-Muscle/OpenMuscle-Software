# om_network.py - Unified network manager for all OpenMuscle devices
#
# Merges FlexGrid V1's async WiFi+UDP with LASK5's ESPNOW capabilities.
# Devices that only need WiFi+UDP can ignore the ESPNOW methods.

import network
import socket
import uasyncio as asyncio
import om_logger as log

class NetworkManager:
    def __init__(self, settings):
        """
        Args:
            settings: Settings instance with wifi_ssid, wifi_password,
                      udp_target_ip, udp_port keys
        """
        self.ssid = settings.get("wifi_ssid", "").strip()
        self.password = settings.get("wifi_password", "").strip()
        self.udp_ip = settings.get("udp_target_ip", "255.255.255.255")
        self.udp_port = settings.get("udp_port", 3141)

        self.sta = network.WLAN(network.STA_IF)
        self._sock = None
        self._espnow = None
        self._espnow_peers = set()  # track which MACs we've registered

    async def connect_wifi(self, timeout_s=20):
        """Connect to WiFi and open a UDP socket. Raises RuntimeError on failure."""
        if not self.ssid:
            log.warn("No Wi-Fi SSID configured; skipping connection")
            return False

        if not self.sta.active():
            self.sta.active(True)

        if not self.sta.isconnected():
            log.info("Connecting to Wi-Fi SSID='{}'".format(self.ssid))
            self.sta.connect(self.ssid, self.password)
            for _ in range(timeout_s):
                if self.sta.isconnected():
                    break
                await asyncio.sleep(1)

            if not self.sta.isconnected():
                raise RuntimeError("Wi-Fi connection failed")

        log.info("Wi-Fi connected, IP: " + self.sta.ifconfig()[0])
        self._open_udp()
        return True

    def _open_udp(self):
        if not self._sock:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setblocking(False)

    async def send_udp(self, payload_bytes):
        """Send raw bytes over UDP. payload_bytes should come from om_packet.build_packet()."""
        if not self._sock:
            return
        try:
            self._sock.sendto(payload_bytes, (self.udp_ip, self.udp_port))
        except Exception as e:
            log.error("UDP send error: " + str(e))

    def send_udp_sync(self, payload_bytes):
        """Synchronous UDP send for devices that don't use async loops."""
        if not self._sock:
            self._open_udp()
        try:
            self._sock.sendto(payload_bytes, (self.udp_ip, self.udp_port))
        except Exception as e:
            log.error("UDP send error: " + str(e))

    async def send_udp_to_subscribers(self, payload_bytes, subscribers):
        """Fan out one UDP payload to every Wi-Fi subscriber in the list.

        New in the discovery + subscribe protocol (spec section 5.1). The
        existing send_udp() to a hardcoded target is kept for backward
        compat with FlexGrid V1 and any device that has not yet been
        migrated to discovery.

        subscribers: om_subscribers.Subscribers instance.
        """
        if not self._sock:
            return
        targets = subscribers.wifi_targets()
        if not targets:
            return
        for host, port in targets:
            try:
                self._sock.sendto(payload_bytes, (host, port))
            except OSError as e:
                errno = getattr(e, "errno", None) or (e.args[0] if e.args else None)
                # ENOMEM (12) and EAGAIN (11) are non-fatal; next iter retries.
                if errno not in (11, 12):
                    log.warn("UDP send to {}:{} failed errno={} {}".format(host, port, errno, e))
            except Exception as e:
                log.warn("UDP send to {}:{} unexpected: {}".format(host, port, e))

    # --- ESPNOW (optional, for devices that need P2P communication) ---

    def init_espnow(self):
        """Initialize ESPNOW. Call this only on devices that need P2P."""
        try:
            import espnow
            if not self.sta.active():
                self.sta.active(True)
            self._espnow = espnow.ESPNow()
            self._espnow.active(True)
            log.info("ESPNOW initialized")
        except ImportError:
            log.warn("ESPNOW not available on this platform")

    def espnow_add_peer(self, mac):
        if self._espnow:
            try:
                self._espnow.add_peer(mac)
            except Exception:
                pass

    def espnow_send(self, mac, data):
        """Send data via ESPNOW. data can be bytes or str.

        Auto-registers the peer on first send -- ESPNow requires `add_peer()`
        before `send()`, including for the broadcast MAC `b'\\xff'*6`. We
        track registered peers in a set so the add_peer call is once-per-mac
        rather than on every send.
        """
        if not self._espnow:
            return
        if mac not in self._espnow_peers:
            try:
                self._espnow.add_peer(mac)
            except Exception:
                # Already added (or unsupported); cache to suppress retries.
                pass
            self._espnow_peers.add(mac)
        if isinstance(data, str):
            data = data.encode()
        try:
            self._espnow.send(mac, data)
        except Exception as e:
            log.warn("ESPNow send error: " + str(e))

    def espnow_recv(self, timeout=0):
        """Non-blocking ESPNOW receive. Returns (mac, msg) or None."""
        if not self._espnow:
            return None
        try:
            host, msg = self._espnow.recv(timeout)
            if msg:
                return (host, msg)
        except Exception:
            pass
        return None

    def espnow_broadcast(self, data):
        """Broadcast data to all ESPNOW peers."""
        self.espnow_send(b'\xff\xff\xff\xff\xff\xff', data)

    # --- Utility ---

    def get_ip(self):
        return self.sta.ifconfig()[0] if self.sta.isconnected() else None

    def is_connected(self):
        return self.sta.isconnected()

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = None
        if self._espnow:
            self._espnow.active(False)
            self._espnow = None

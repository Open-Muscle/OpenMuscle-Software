# Network Manager V1.1.0
# Designed on OM Labeler V1.1.0

import network
import espnow
import socket
import time

class NetworkManager:
    def __init__(self):
        self.wlan = network.WLAN(network.STA_IF)
        self.e = None
        self.peers = set()
        self.message_queue = []
        self.device_id = self.wlan.config('mac')  # Use MAC address as device ID
        self.udp_socket = None
        self.encryption_key = 42  # Simple key for XOR cipher
        self.init_espnow()

    def init_espnow(self):
        """Initializes the ESPNow interface."""
        self.wlan.active(True)
        self.e = espnow.ESPNow()
        self.e.active(True)  # Activate ESPNow
        # Since e.on_recv() is not available, we'll handle incoming messages in the main loop

    def wifi_connect(self, ssid, password, timeout=10):
        """Connects to a Wi-Fi network."""
        print("Connecting to Wi-Fi...")
        self.wlan.active(True)
        self.wlan.connect(ssid, password)
        
        start_time = time.time()
        while not self.wlan.isconnected():
            if time.time() - start_time > timeout:
                print("Failed to connect to Wi-Fi.")
                return None
            time.sleep(0.5)

        print("Wi-Fi Connected!")
        print(f"SSID: {ssid}")
        print("Network Config:", self.wlan.ifconfig())
        return self.wlan.ifconfig()

    def espnow_send(self, mac_address, message):
        """Sends a message to a specific MAC address over ESPNow."""
        if mac_address not in self.peers:
            self.e.add_peer(mac_address)
            self.peers.add(mac_address)
        self.e.send(mac_address, message.encode())

    def get_next_message(self):
        """Checks for incoming ESPNow messages."""
        try:
            host, msg = self.e.recv(0)  # Non-blocking receive
            if msg:
                message = msg.decode()
                if host not in self.peers:
                    self.e.add_peer(host)
                    self.peers.add(host)
                print(f"ESPNow message from {host}: {message}")
                return (host, message)
        except Exception as e:
            pass  # No message received
        return None

    def send_hello(self):
        """Broadcasts a HELLO message to announce presence."""
        broadcast_mac = b'\xff\xff\xff\xff\xff\xff'
        self.e.send(broadcast_mac, "HELLO".encode())

    def request_wifi_credentials(self):
        """Broadcasts a REQUEST_WIFI message."""
        broadcast_mac = b'\xff\xff\xff\xff\xff\xff'
        self.e.send(broadcast_mac, "REQUEST_WIFI".encode())

    def xor_encrypt(self, data, key):
        return bytes([b ^ key for b in data])

    def xor_decrypt(self, data, key):
        return bytes([b ^ key for b in data])

    def get_battery_level(self):
        """Mock method to get battery level."""
        return 100  # Replace with actual battery reading

    def get_memory_usage(self):
        """Mock method to get memory usage."""
        import gc
        gc.collect()
        free_memory = gc.mem_free()
        return free_memory

    def start_task(self, task_id):
        """Starts the specified task."""
        print(f"Starting task {task_id}")
        # Implement the actual task logic here

    def send_health_check(self, mac_address):
        """Sends a HEALTH_CHECK message to a specific device."""
        self.espnow_send(mac_address, "HEALTH_CHECK")

    def send_execute_task(self, mac_address, task_id):
        """Sends an EXECUTE_TASK command to a specific device."""
        self.espnow_send(mac_address, f"EXECUTE_TASK:{task_id}")

    def socket_open(self):
        """Opens a UDP socket for fast sending."""
        if self.udp_socket is None:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            print("UDP socket opened.")

    def udp_fast_send(self, ip, port, message):
        """Sends a UDP packet quickly to the specified IP and port using an open socket."""
        if self.udp_socket:
            self.udp_socket.sendto(message.encode(), (ip, port))
        else:
            print("Error: UDP socket is not open. Call socket_open() first.")

    def socket_close(self):
        """Closes the UDP socket."""
        if self.udp_socket:
            self.udp_socket.close()
            self.udp_socket = None
            print("UDP socket closed.")
import socket
import time
import binascii  # For hex dumping raw bytes

def get_local_ip():
    """Retrieve the local IP address of the computer."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to an external host to get the local IP address
        s.connect(('8.8.8.8', 80))  # Google's public DNS server
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = '127.0.0.1'  # Fallback to localhost
    finally:
        s.close()
    return local_ip

def main():
    local_ip = get_local_ip()  # Use the actual local IP for display, but bind to 0.0.0.0 to listen on all interfaces
    port = 3141  # Adjust if needed; was 3141 in previous setups—confirm with device settings
    
    # Create a UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)  # Set a 5-second timeout to allow periodic status prints without blocking forever
    
    # Bind the socket to all interfaces and the port
    sock.bind(('0.0.0.0', port))

    print(f"Listening for UDP packets on all interfaces (primary local IP: {local_ip}), port {port}")
    print("Will print timestamp, sender address, packet size, decoded text (if UTF-8), and raw hex dump for each packet.")
    print("Press Ctrl+C to exit.")

    last_status_time = time.time()
    while True:
        try:
            # Receive data from the socket
            data, addr = sock.recvfrom(4096)  # Increased buffer size to handle larger packets (e.g., your ~225-byte ones)
            current_time = time.time()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(current_time))
            packet_size = len(data)
            
            # Try to decode as UTF-8, fallback to raw hex if not
            try:
                decoded = data.decode('utf-8')
            except UnicodeDecodeError:
                decoded = "Non-UTF8 data"
            
            hex_dump = binascii.hexlify(data).decode('ascii')  # Hex representation for raw inspection
            
            print(f"\n[{timestamp}] Received from {addr}:")
            print(f"  Size: {packet_size} bytes")
            print(f"  Decoded: {decoded}")
            print(f"  Raw Hex: {hex_dump}")
        
        except socket.timeout:
            # Periodic status to confirm listener is alive
            current_time = time.time()
            if current_time - last_status_time > 10:  # Print status every 10 seconds if no packets
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))}] No packets received yet... Still listening.")
                last_status_time = current_time
        
        except KeyboardInterrupt:
            print("\nExiting listener.")
            break

    sock.close()

if __name__ == "__main__":
    main()

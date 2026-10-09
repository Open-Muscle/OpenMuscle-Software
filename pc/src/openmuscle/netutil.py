"""Small network helpers shared by the web server, discovery and `openmuscle doctor`.

Stdlib only. Everything here answers one plain question about this computer's
network ("is this port free", "which address would the band reach me on", "what
Wi-Fi am I on") and never raises into the caller: failures come back as None,
False or an empty value, so callers can turn them into a plain sentence.
"""

import errno
import json
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request

IS_WINDOWS = sys.platform.startswith("win")

# Public addresses used only as a routing hint: a UDP connect() sends no packet,
# it just makes the OS pick the interface it would use to reach that address.
_ROUTE_HINTS = (("8.8.8.8", 80), ("1.1.1.1", 80), ("192.168.1.1", 80), ("10.0.0.1", 80))


# ----- binding ---------------------------------------------------------------

def make_exclusive(sock):
    """Ask the OS for an exclusive bind on `sock` (call before bind()).

    On Windows SO_REUSEADDR lets a second program share (or steal) a port, so a
    second OpenMuscle window would bind the same UDP port and silently split the
    traffic. SO_EXCLUSIVEADDRUSE refuses that. Elsewhere a plain bind with no
    SO_REUSEADDR is already exclusive for UDP.
    """
    if IS_WINDOWS and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        except OSError:
            pass
    return sock


def bind_error_text(port, proto, exc):
    """One plain sentence for a failed bind, naming the likely cause."""
    code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    if code in (errno.EADDRINUSE, 10048, 98, 48):
        return ("%s port %d is already in use, probably by another OpenMuscle "
                "window or another program." % (proto, port))
    if code in (errno.EACCES, 10013, 13):
        return ("%s port %d is not available to this program (another program holds "
                "it exclusively, or Windows has reserved it)." % (proto, port))
    return "%s port %d could not be opened (%s)." % (proto, port, exc)


def udp_bind_check(port, host="0.0.0.0"):
    """(True, "") when an exclusive UDP bind on host:port would succeed right
    now, else (False, plain sentence). Binds and immediately releases."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        make_exclusive(s)
        s.bind((host, port))
        return True, ""
    except OSError as e:
        return False, bind_error_text(port, "UDP", e)
    finally:
        s.close()


def tcp_port_free(host, port):
    """True when a TCP listener could bind host:port right now.

    Windows uses the exclusive bind, so a program already holding 0.0.0.0:port
    is seen even when we ask about 127.0.0.1 (without it Windows lets the second
    bind succeed and the two servers fight over the port).
    """
    bind_host = host if host not in ("", "localhost") else "127.0.0.1"
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if IS_WINDOWS:
            make_exclusive(s)
        else:
            # Same as uvicorn: a lingering TIME_WAIT must not read as "busy".
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((bind_host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


# ----- is it already OpenMuscle? --------------------------------------------

def find_running_instance(port, host="127.0.0.1", timeout=1.0):
    """Ask whatever listens on host:port whether it is an OpenMuscle server.

    Returns the parsed GET /api/mode dict when it is (plain http first, then
    https with an unverified context for the mkcert setup), else None. Older
    servers answer /api/mode with only {"debug": bool}; that still counts.
    """
    import ssl
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    contexts = (("http", None), ("https", insecure))
    for scheme, ctx in contexts:
        url = "%s://%s:%d/api/mode" % (scheme, host, port)
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
                obj = json.loads(r.read(4096).decode("utf-8", "replace"))
        except Exception as e:
            if isinstance(getattr(e, "reason", e), (TimeoutError, socket.timeout))                     or isinstance(e, (TimeoutError, socket.timeout)):
                break          # something is listening but says nothing: not us
            continue
        if isinstance(obj, dict) and (obj.get("app") == "openmuscle"
                                      or isinstance(obj.get("debug"), bool)):
            return obj
    return None


# ----- addresses -------------------------------------------------------------

def is_usable_ipv4(ip):
    """True for a dotted-quad that is neither loopback, unspecified nor link-local."""
    if not ip:
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return False
    if not all(0 <= n <= 255 for n in nums):
        return False
    if nums[0] in (0, 127) or (nums[0] == 169 and nums[1] == 254):
        return False
    return True


def local_ip_for(target_ip, port=9):
    """The address of this computer on the interface that reaches `target_ip`.

    A UDP connect() to the device's own address reads the right interface on
    any subnet (home router, the band's own 192.168.4.x access point, a phone
    hotspot) without sending a packet. Returns None when there is no route.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target_ip, port))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def default_lan_ip(test_target=None):
    """Best-effort address of this computer on its main network, or 127.0.0.1.

    Asks the OS which interface it would use toward the internet (and a couple
    of private gateways), so it does not depend on one particular router
    address. No packet is sent. Falls back to hostname resolution, then loopback.
    """
    targets = [test_target] if test_target else list(_ROUTE_HINTS)
    for target in targets:
        ip = local_ip_for(target[0], target[1])
        if is_usable_ipv4(ip):
            return ip
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if is_usable_ipv4(ip):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def local_ipv4_addresses():
    """Usable IPv4 addresses of this computer (best effort, no extra packages)."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if is_usable_ipv4(ip) and ip not in found:
                found.append(ip)
    except Exception:
        pass
    main = default_lan_ip()
    if is_usable_ipv4(main) and main not in found:
        found.insert(0, main)
    return found


def subnet_of(ip):
    """The /24 prefix of a usable IPv4 ("192.168.4.7" -> "192.168.4"), else None."""
    if not is_usable_ipv4(ip):
        return None
    return ".".join(ip.split(".")[:3])


# ----- running commands ------------------------------------------------------

def run_cmd(args, timeout=4.0):
    """Run a command, return (returncode, stdout text). (None, "") when it is
    missing, times out or fails to start. Never opens a console window."""
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x08000000     # CREATE_NO_WINDOW
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)
    except Exception:
        return None, ""
    out = p.stdout.decode("utf-8", "replace") if p.stdout else ""
    return p.returncode, out


# ----- Wi-Fi name and adapter -------------------------------------------------

def parse_wifi_ssid(platform, text):
    """Pull the current Wi-Fi name out of the platform's command output."""
    if not text:
        return None
    if platform == "windows":
        # netsh wlan show interfaces: "    SSID                   : name" (and a
        # separate BSSID line that must not match).
        for line in text.splitlines():
            m = re.match(r"^\s*SSID\s*:\s*(.+?)\s*$", line)
            if m:
                return m.group(1) or None
        return None
    if platform == "darwin":
        m = re.search(r"Current Wi-Fi Network:\s*(.+?)\s*$", text, re.M)
        return m.group(1) if m else None
    # nmcli -t -f active,ssid dev wifi  ->  "yes:name" (colons in names are escaped)
    for line in text.splitlines():
        if line.startswith("yes:"):
            name = line[4:].replace("\\:", ":").strip()
            return name or None
    return None


def platform_name():
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def wifi_ssid():
    """The Wi-Fi network this computer is on, or None (wired, off, or unknown)."""
    plat = platform_name()
    if plat == "windows":
        _rc, out = run_cmd(["netsh", "wlan", "show", "interfaces"], 4.0)
    elif plat == "darwin":
        _rc, out = run_cmd(["networksetup", "-getairportnetwork", "en0"], 3.0)
    else:
        _rc, out = run_cmd(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"], 3.0)
    return parse_wifi_ssid(plat, out)


def parse_adapter_for_ip(platform, text, ip):
    """Adapter name that owns `ip`, from ipconfig / ifconfig / `ip -o addr` output."""
    if not text or not ip:
        return None
    if platform == "windows":
        adapter = None
        for line in text.splitlines():
            if line and not line[0].isspace() and line.rstrip().endswith(":"):
                adapter = line.rstrip().rstrip(":")
                adapter = re.sub(r"^.*adapter\s+", "", adapter, flags=re.I)
            elif adapter and re.search(r"(^|[\s:])%s(\s|\(|$)" % re.escape(ip), line):
                return adapter
        return None
    if platform == "darwin":
        iface = None
        for line in text.splitlines():
            m = re.match(r"^(\w+):", line)
            if m:
                iface = m.group(1)
            elif iface and re.search(r"\binet %s\b" % re.escape(ip), line):
                return iface
        return None
    for line in text.splitlines():           # "2: wlan0    inet 192.168.1.5/24 ..."
        m = re.match(r"^\d+:\s+(\S+)\s+inet\s+%s/" % re.escape(ip), line)
        if m:
            return m.group(1)
    return None


_ADAPTER_CACHE = {"ip": None, "t": 0.0, "name": None}
_ADAPTER_LOCK = threading.Lock()


def adapter_name_for_ip(ip, max_age=30.0):
    """Name of the network adapter holding `ip` (cached; one command run)."""
    with _ADAPTER_LOCK:
        c = _ADAPTER_CACHE
        if c["ip"] == ip and time.time() - c["t"] < max_age:
            return c["name"]
    plat = platform_name()
    if plat == "windows":
        _rc, out = run_cmd(["ipconfig"], 4.0)
    elif plat == "darwin":
        _rc, out = run_cmd(["ifconfig"], 4.0)
    else:
        _rc, out = run_cmd(["ip", "-o", "-4", "addr", "show"], 3.0)
    name = parse_adapter_for_ip(plat, out, ip)
    with _ADAPTER_LOCK:
        _ADAPTER_CACHE.update(ip=ip, t=time.time(), name=name)
    return name


# ----- rate counter ----------------------------------------------------------

class RateWindow:
    """Events per minute over a rolling 60 s window, cheap enough for the UDP
    receive path (one dict update per event)."""

    def __init__(self, seconds=60):
        self._n = seconds
        self._counts = [0] * seconds
        self._stamps = [0] * seconds
        self._lock = threading.Lock()

    def add(self, n=1, now=None):
        sec = int(now if now is not None else time.time())
        i = sec % self._n
        with self._lock:
            if self._stamps[i] != sec:
                self._stamps[i] = sec
                self._counts[i] = 0
            self._counts[i] += n

    def per_minute(self, now=None):
        sec = int(now if now is not None else time.time())
        with self._lock:
            return sum(c for c, s in zip(self._counts, self._stamps)
                       if 0 <= sec - s < self._n)

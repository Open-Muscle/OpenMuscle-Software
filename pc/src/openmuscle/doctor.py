"""Self-diagnosis: "why can't I see my band?" in plain words.

One backend, three front doors:
  * `openmuscle doctor`            the command line (pass / warn / fail lines)
  * GET /api/discovery/diagnose    the in-app panel, same checks, JSON
  * POST /api/support-report       a zip of versions, results and recent logs

run_doctor() returns this JSON shape (the UI relies on it):

  {ok, checks: [{id, title, status: pass|warn|fail, detail, fix}],
   udp_port, bound_host, devices_seen: [{device_id, ip, age_s}],
   last_announce_s: number|null,
   firewall: {platform, likely_blocked, how_to_fix},
   likely_cause, advice: {sentence, action}}

likely_cause is one of: none, firewall, different_network, client_isolation,
device_offline, port_conflict, no_network. `advice` is the one plain sentence
and the one action to show for it.

Reports never contain Wi-Fi names, passwords or capture data: the Wi-Fi name is
shown on screen but replaced in anything written to a file, and logs are
scrubbed of file names and secrets before they go into a report.
"""

import importlib.metadata
import importlib.util
import json
import os
import platform as _platform
import re
import socket
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

from openmuscle import firewall as fw
from openmuscle import netutil, paths

PASS, WARN, FAIL = "pass", "warn", "fail"

FRESH_S = 15.0           # a device heard within this many seconds is "here"
BEACON_RECENT_S = 10.0   # "any beacon in the last 10 s"
DATA_FLOW_S = 5.0        # frames within this many seconds count as flowing
OVERALL_BUDGET_S = 9.0   # the whole run stays under 10 s

CAUSES = ("none", "firewall", "different_network", "client_isolation",
          "device_offline", "port_conflict", "no_network")

_GUEST_HINT = re.compile(r"guest|visitor|public|hotel|airport|captive",
                         re.I)


def _chk(cid, title, status, detail="", fix=""):
    return {"id": cid, "title": title, "status": status,
            "detail": detail, "fix": fix}


# ----- individual checks ------------------------------------------------------

def check_python():
    v = sys.version_info
    ver = "%d.%d.%d" % (v[0], v[1], v[2])
    if (v[0], v[1]) >= (3, 10):
        return _chk("python", "Python version", PASS, "Python %s" % ver)
    return _chk("python", "Python version", FAIL,
                "Python %s is too old; OpenMuscle needs 3.10 or newer." % ver,
                "Install Python 3.10 or newer from python.org, then reinstall "
                "OpenMuscle.")


def check_packages():
    need = ["fastapi", "uvicorn", "numpy", "pandas", "sklearn", "click", "matplotlib"]
    missing = [m for m in need if importlib.util.find_spec(m) is None]
    if not missing:
        return _chk("packages", "Required packages", PASS,
                    "All required Python packages are installed.")
    return _chk("packages", "Required packages", FAIL,
                "Missing: " + ", ".join(missing),
                "Reinstall OpenMuscle: pip install --upgrade --force-reinstall openmuscle")


def check_static():
    try:
        import openmuscle.web as web_pkg
        static = os.path.join(os.path.dirname(web_pkg.__file__), "static")
    except Exception:
        static = ""
    wanted = ["index.html", "app.js", "styles.css", "guide.js"]
    missing = [n for n in wanted
               if not (static and os.path.isfile(os.path.join(static, n))
                       and os.path.getsize(os.path.join(static, n)) > 0)]
    if not missing:
        return _chk("static_files", "Web page files", PASS,
                    "The browser page files are installed.")
    return _chk("static_files", "Web page files", FAIL,
                "Missing page files: " + ", ".join(missing),
                "Reinstall OpenMuscle: pip install --upgrade --force-reinstall openmuscle")


def check_data_folder():
    root = paths.data_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".om_doctor_", dir=str(root))
        os.close(fd)
        os.unlink(tmp)
    except OSError as e:
        return _chk("data_folder", "Data folder", FAIL,
                    "Cannot write to %s (%s)." % (root, e.strerror or e),
                    "Pick a folder you can write to: openmuscle web --data-dir "
                    "<folder>, or set OPENMUSCLE_HOME.")
    return _chk("data_folder", "Data folder", PASS,
                "Recordings are saved in %s and the folder is writable." % root)


def check_http_port(ctx):
    port = ctx["http_port"]
    title = "Browser page port (%d)" % port
    if ctx["running"]:
        return _chk("port_http", title, PASS, "OpenMuscle is serving on port %d." % port)
    if netutil.tcp_port_free("127.0.0.1", port):
        return _chk("port_http", title, PASS, "Port %d is free." % port)
    inst = netutil.find_running_instance(port)
    if inst:
        return _chk("port_http", title, PASS,
                    "OpenMuscle is already running at http://localhost:%d." % port)
    if ctx["http_explicit"]:
        return _chk("port_http", title, FAIL,
                    "Another program is using port %d." % port,
                    "Close that program, or start OpenMuscle on another port: "
                    "openmuscle web --port %d" % (port + 10))
    return _chk("port_http", title, WARN,
                "Another program is using port %d, so OpenMuscle will use the next "
                "free port." % port)


def _udp_port_check(ctx, cid, title, port, role, listener_ok, listener_error,
                    purpose):
    """Shared by the data (3141) and beacon (3140) port checks."""
    if ctx["running"]:
        if listener_ok is False:
            return _chk(cid, title, FAIL, listener_error or
                        "UDP port %d could not be opened." % port,
                        "Close the other OpenMuscle window (or the program using the "
                        "port), then restart. To use another port: "
                        "openmuscle web --%s %d" % (role, port + 10))
        return _chk(cid, title, PASS, "OpenMuscle is listening on UDP %d (%s)."
                    % (port, purpose))
    ok, err = netutil.udp_bind_check(port)
    if ok:
        return _chk(cid, title, PASS, "UDP port %d is free (%s)." % (port, purpose))
    if netutil.find_running_instance(ctx["http_port"]):
        return _chk(cid, title, WARN,
                    "UDP %d is held by the OpenMuscle already running in another "
                    "window. That is fine; close it if you want to test from here."
                    % port)
    return _chk(cid, title, FAIL, err,
                "Close the program using UDP %d (another OpenMuscle window is the "
                "usual one). On Windows: netstat -ano | findstr :%d  shows which "
                "program. Or use another port: openmuscle web --%s %d"
                % (port, port, role, port + 10))


def check_lan(ctx):
    ip = ctx["pc_host"]
    if netutil.is_usable_ipv4(ip):
        return _chk("lan_ip", "Network address", PASS,
                    "This computer's address is %s." % ip)
    return _chk("lan_ip", "Network address", FAIL,
                "This computer has no network address (it is not on a network).",
                "Connect to the same Wi-Fi network as the bands, then run this "
                "again.")


def check_wifi(ssid, redact):
    if not ssid:
        return _chk("wifi", "Wi-Fi network", WARN,
                    "No Wi-Fi network detected (a wired connection is fine if the "
                    "bands are on the same network).",
                    "Make sure this computer and the bands are on the same Wi-Fi.")
    if ssid.upper().startswith("OM-"):
        return _chk("wifi", "Wi-Fi network", WARN,
                    "This computer is connected to a band's own setup network "
                    "(%s), not your normal Wi-Fi." % ("OM-..." if redact else ssid),
                    "Reconnect this computer to your normal Wi-Fi, and forget the "
                    "OM- network in the Wi-Fi list.")
    shown = "(name hidden in reports)" if redact else "'%s'" % ssid
    return _chk("wifi", "Wi-Fi network", PASS,
                "Connected to Wi-Fi network %s." % shown)


def check_firewall(plat, fwinfo, traffic_ok):
    title = "Firewall"
    how = firewall_advice(plat, fwinfo)
    if plat == "windows":
        if fwinfo is None or not fwinfo.get("readable"):
            return _chk("firewall", title, WARN,
                        "Could not read the Windows firewall rules.", how)
        ports = fwinfo["ports"]
        if traffic_ok:
            return _chk("firewall", title, PASS,
                        "Bands are reaching this computer, so the firewall is "
                        "letting them in.")
        if all(ports.get(p) for p in fw.PORTS):
            return _chk("firewall", title, PASS,
                        "Windows allows incoming UDP 3140 and 3141 on the %s network."
                        % (fwinfo.get("profile") or "current"))
        prof = fwinfo.get("profile") or "current"
        if ports.get(3141) and not ports.get(3140):
            detail = ("Windows allows data on UDP 3141 but not discovery beacons on "
                      "UDP 3140, so bands will not announce themselves.")
        elif fwinfo.get("other_profile_rule"):
            detail = ("OpenMuscle is allowed on another network type, but this "
                      "network is set to %s." % prof.capitalize())
        else:
            detail = ("No Windows firewall rule lets bands send to this computer "
                      "(UDP 3140 and 3141) on the %s network." % prof)
        return _chk("firewall", title, FAIL, detail, how)
    if plat == "darwin":
        if fwinfo and fwinfo.get("mac_enabled"):
            return _chk("firewall", title, WARN,
                        "The macOS firewall is on; it must allow incoming "
                        "connections for Python.", how)
        return _chk("firewall", title, PASS, "The macOS firewall is off or not "
                    "blocking Python.")
    if fwinfo and fwinfo.get("tool"):
        return _chk("firewall", title, WARN,
                    "%s is installed; if bands are not found it may be blocking "
                    "UDP 3140 and 3141." % fwinfo["tool"], how)
    return None


def check_internet(hosts):
    if not hosts:
        return None
    ok = [h for h, up in hosts.items() if up]
    if len(ok) == len(hosts):
        return _chk("internet", "Internet for the 3D hand viewer", PASS,
                    "Reached %s." % ", ".join(sorted(hosts)))
    return _chk("internet", "Internet for the 3D hand viewer", WARN,
                "Could not reach %s. The 3D hand view needs internet; everything "
                "else works offline." % ", ".join(sorted(h for h in hosts
                                                         if h not in ok)),
                "Connect this computer to the internet if you want the 3D hand view.")


def check_loopback():
    """Send one real sensor frame to a throwaway listener on this computer and
    make sure it comes out parsed: proves the sockets and the parser work."""
    from openmuscle.receiver.udp_listener import UDPListener
    title = "Self-test frame"
    listener = UDPListener(port=0, bind_ip="127.0.0.1", quiet=True)
    try:
        listener.start()
        if not listener.bound:
            return _chk("loopback", title, FAIL, listener.bind_error or
                        "Could not open a test socket.",
                        "Check that no security software is blocking Python.")
        frame = json.dumps({"v": "1.0", "type": "flexgrid", "id": "flexgrid-doctor",
                            "ts": 1, "data": {"matrix": [[0] * 4 for _ in range(15)]}})
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            tx.sendto(frame.encode("utf-8"), ("127.0.0.1", listener.bound_port))
        finally:
            tx.close()
        deadline = time.time() + 1.5
        while time.time() < deadline:
            if not listener.packet_queue.empty():
                pkt = listener.packet_queue.get()
                if pkt.device_id == "flexgrid-doctor":
                    return _chk("loopback", title, PASS,
                                "A test sensor frame was sent and received on this "
                                "computer.")
            time.sleep(0.02)
        return _chk("loopback", title, FAIL,
                    "A test frame sent to this computer never arrived.",
                    "Security software may be blocking Python's network access; "
                    "allow Python, then try again.")
    finally:
        listener.stop()


# ----- evidence about the bands ---------------------------------------------

def _known_devices(ctx):
    """Devices heard this run plus devices remembered from earlier runs that have
    not answered. Each: device_id, ip, cmd_port, age_s (None if never heard now),
    heard (bool)."""
    out = {}
    disc = ctx["discovery"]
    now = time.time()
    if disc is not None:
        for d in disc.snapshot():
            out[d["device_id"]] = {
                "device_id": d["device_id"], "ip": d["ip"],
                "cmd_port": d.get("cmd_port") or 8001, "age_s": d.get("age_s"),
                "source": d.get("source") or "",
                "subscribed": bool(d.get("subscribed")), "heard": True}
    try:
        from openmuscle.discovery.manager import default_cache_path
        with open(ctx.get("cache_path") or default_cache_path(), "r",
                  encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        cached = []
    for e in cached if isinstance(cached, list) else []:
        did = e.get("device_id")
        if not did or did in out or not e.get("ip"):
            continue
        last = e.get("last_contact") or 0
        out[did] = {"device_id": did, "ip": e["ip"],
                    "cmd_port": e.get("cmd_port") or 8001,
                    "age_s": round(now - last, 2) if last else None,
                    "source": "cache", "subscribed": False, "heard": False}
    return list(out.values())


def _tcp_reachable(ip, port, timeout=1.2):
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def gather_bands(ctx):
    """Send a discover probe, then collect who is here and who answers on TCP."""
    disc = ctx["discovery"]
    if ctx["discover"]:
        try:
            if disc is None:
                from openmuscle.discovery.manager import DiscoveryManager
                disc = DiscoveryManager(
                    pc_host=ctx.get("pc_host_pin"), udp_port=ctx["udp_port"],
                    announce_port=ctx["announce_port"], cache_path="",
                    auto_subscribe=False)
                ctx["discovery"] = disc
                ctx["temp_discovery"] = True
            disc.send_discover(wait_s=1.5)
        except Exception:
            pass
    devices = _known_devices(ctx)
    if devices:
        with ThreadPoolExecutor(max_workers=min(8, len(devices))) as pool:
            oks = list(pool.map(lambda d: _tcp_reachable(d["ip"], d["cmd_port"]),
                                devices))
        for d, ok in zip(devices, oks):
            d["tcp_ok"] = ok
    return devices


# ----- turning evidence into one plain cause ---------------------------------

def infer_cause(facts):
    """Pick the single most likely reason bands are not showing up.

    facts: lan_ip_ok, listener_ok (False if a bind failed), firewall_blocked,
    beacon_recent, data_flowing, ssid, pc_host, devices [{device_id, ip, age_s,
    tcp_ok, subscribed}].
    Returns (cause, sentence, action).
    """
    devices = facts.get("devices") or []
    pc_net = netutil.subnet_of(facts.get("pc_host"))
    # "Here": announced itself by broadcast a moment ago. A device only found by
    # a TCP probe, or remembered from last time, says nothing about broadcasts
    # reaching us, and one on this very computer (a simulator) proves nothing
    # about the network.
    fresh = [d for d in devices if d.get("age_s") is not None
             and d["age_s"] < FRESH_S and d.get("heard", True)
             and d.get("source", "beacon") == "beacon"
             and not d["ip"].startswith("127.")]
    on_this_computer = [d for d in devices if d["ip"].startswith("127.")
                        and d.get("heard", True) and d.get("tcp_ok", True)]

    if not facts.get("lan_ip_ok", True):
        return ("no_network", "This computer is not connected to a network.",
                "Connect to the same Wi-Fi as the bands.")
    if facts.get("listener_ok") is False:
        return ("port_conflict",
                "OpenMuscle could not open the network port the bands send to.",
                "Close the other OpenMuscle window or program using it, then restart.")
    if fresh:
        if all(not d.get("tcp_ok", True) for d in fresh):
            return ("client_isolation",
                    "The band is announcing itself, but this computer cannot "
                    "connect to it, which usually means the Wi-Fi network keeps "
                    "devices apart (guest network or client isolation).",
                    "Use your main Wi-Fi instead of a guest network, or turn off "
                    "'client isolation' / 'AP isolation' in the router settings.")
        subscribed = [d for d in fresh if d.get("subscribed")
                      and not d["ip"].startswith("127.")]
        if subscribed and not facts.get("data_flowing"):
            return ("firewall",
                    "The band is found and connected, but its data is not arriving, "
                    "so the firewall is probably blocking incoming UDP data.",
                    "Allow OpenMuscle through the firewall (Windows: run "
                    "'openmuscle firewall').")
        return ("none", "Bands are reaching this computer.", "")
    if facts.get("data_flowing"):
        return ("none", "Data is arriving from a band.", "")
    if on_this_computer:
        return ("none", "A simulated band on this computer is connected.", "")
    if facts.get("firewall_blocked"):
        return ("firewall",
                "The firewall is probably blocking the bands from reaching this "
                "computer.",
                "Allow OpenMuscle through the firewall (Windows: run "
                "'openmuscle firewall').")
    elsewhere = [d for d in devices
                 if netutil.subnet_of(d["ip"]) and pc_net
                 and netutil.subnet_of(d["ip"]) != pc_net
                 and not d.get("tcp_ok")]
    if elsewhere:
        d = elsewhere[0]
        return ("different_network",
                "A band was last seen at %s, which is on a different network from "
                "this computer (%s)." % (d["ip"], facts.get("pc_host")),
                "Connect this computer to the same Wi-Fi as the band.")
    # (A simulator on this same computer is never behind the firewall.)
    reachable = [d for d in devices if d.get("tcp_ok") and not d["ip"].startswith("127.")]
    if reachable:
        return ("firewall",
                "A band answers when asked, but nothing it sends is reaching this "
                "computer, so the firewall is probably blocking incoming UDP.",
                "Allow OpenMuscle through the firewall (Windows: run "
                "'openmuscle firewall').")
    ssid = facts.get("ssid") or ""
    if ssid and _GUEST_HINT.search(ssid) and not ssid.upper().startswith("OM-"):
        return ("client_isolation",
                "No band answered, and this looks like a guest or public Wi-Fi "
                "network, which often keeps devices apart.",
                "Use your main Wi-Fi instead of a guest network.")
    if devices:
        d = devices[0]
        return ("device_offline",
                "A band was seen before at %s but does not answer now." % d["ip"],
                "Check the band is powered on and shows it joined the Wi-Fi.")
    return ("device_offline",
            "No band has answered on this network.",
            "Check the band is powered on and shows it joined your Wi-Fi, and "
            "that this computer is on the same Wi-Fi.")


def firewall_advice(plat, fwinfo=None):
    if plat == "windows":
        text = ("Run 'openmuscle firewall' and answer yes to the Windows prompt; it "
                "allows UDP 3140 and 3141 for Private networks.")
        if fwinfo and fwinfo.get("readable") and \
                (fwinfo.get("profile") or "").lower() == "public":
            text += (" Your network is set to Public: change it to Private in "
                     "Windows Settings > Network & internet > Wi-Fi > your network, "
                     "or run 'openmuscle firewall --public'.")
        return text
    if plat == "darwin":
        return ("Open System Settings > Network > Firewall > Options and allow "
                "incoming connections for Python, or turn the firewall off while "
                "you set up.")
    return ("Allow UDP ports 3140 and 3141 in your firewall, for example: "
            "sudo ufw allow 3140:3141/udp")


# ----- assembling the result -------------------------------------------------

def _firewall_probe(plat):
    if plat == "windows":
        return fw.inspect(timeout=5.0)
    if plat == "darwin":
        _rc, out = netutil.run_cmd(
            ["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"], 3.0)
        return {"mac_enabled": "enabled" in (out or "").lower()
                and "disabled" not in (out or "").lower()}
    import shutil
    for tool in ("ufw", "firewall-cmd"):
        if shutil.which(tool):
            return {"tool": tool}
    return {}


def _internet_probe():
    hosts = {}
    try:
        import openmuscle.web as web_pkg
        index = os.path.join(os.path.dirname(web_pkg.__file__), "static", "index.html")
        with open(index, "r", encoding="utf-8") as f:
            html = f.read()
        for m in re.finditer(r"https://([A-Za-z0-9.-]+)/[^\"'\s]*\.js", html):
            hosts[m.group(1)] = False
    except OSError:
        return {}

    def _up(host):
        try:
            with socket.create_connection((host, 443), timeout=2.0):
                return True
        except OSError:
            return False

    for h in list(hosts):
        hosts[h] = _up(h)
    return hosts


def _make_ctx(http_port, udp_port, announce_port, http_explicit, discovery,
              data_listener, redact, discover, cache_path):
    return {
        "http_port": http_port, "udp_port": udp_port, "announce_port": announce_port,
        "http_explicit": http_explicit, "discovery": discovery,
        "data_listener": data_listener, "running": data_listener is not None
        or (discovery is not None),
        "redact": redact, "discover": discover, "cache_path": cache_path,
        "pc_host": (discovery.pc_host if discovery is not None
                    else netutil.default_lan_ip()),
        "temp_discovery": False,
    }


def run_doctor(http_port=8000, udp_port=3141, announce_port=3140,
               http_explicit=False, discovery=None, data_listener=None,
               redact=False, discover=True, cache_path=None):
    """Run every check and return the result dict described in the module doc.

    discovery / data_listener: pass the running server's objects to diagnose it
    in place (its ports are then judged by whether they bound, not by trying to
    bind them again). Without them the checks stand alone, as `openmuscle doctor`
    does. redact=True hides the Wi-Fi name (for anything written to a file).
    """
    t0 = time.time()
    ctx = _make_ctx(http_port, udp_port, announce_port, http_explicit, discovery,
                    data_listener, redact, discover, cache_path)
    plat = netutil.platform_name()
    results = {}

    def _task(name, fn):
        def _run():
            try:
                results[name] = fn()
            except Exception as e:           # a check must never break the run
                results[name] = {"_error": "%s: %s" % (type(e).__name__, e)}
        t = threading.Thread(target=_run, name="om-doctor-" + name, daemon=True)
        t.start()
        return t

    threads = [
        _task("wifi", netutil.wifi_ssid),
        _task("fw", lambda: _firewall_probe(plat)),
        _task("internet", _internet_probe),
        _task("loopback", check_loopback),
        _task("bands", lambda: gather_bands(ctx)),
    ]
    for t in threads:
        t.join(max(0.1, OVERALL_BUDGET_S - (time.time() - t0)))

    def got(name, default=None):
        v = results.get(name)
        if v is None or (isinstance(v, dict) and "_error" in v):
            return default
        return v

    ssid = got("wifi")
    fwinfo = got("fw")
    devices = got("bands", [])
    ctx_disc = ctx["discovery"]

    # ---- evidence
    now = time.time()
    last_announce_s = None
    if ctx_disc is not None and getattr(ctx_disc, "last_announce_time", 0):
        last_announce_s = round(now - ctx_disc.last_announce_time, 2)
    beacon_recent = last_announce_s is not None and last_announce_s < BEACON_RECENT_S
    data_flowing = False
    if data_listener is not None:
        age = data_listener.health(now).get("last_packet_age_s")
        data_flowing = age is not None and age < DATA_FLOW_S
    data_ok = (data_listener.bound if data_listener is not None else None)
    beacon_ok = (ctx_disc.listener_ok if (discovery is not None) else None)
    traffic_ok = beacon_recent or data_flowing

    # ---- checks
    checks = [check_python(), check_packages(), check_static(), check_data_folder(),
              check_http_port(ctx)]
    checks.append(_udp_port_check(
        ctx, "port_udp_data", "Sensor data port (UDP %d)" % udp_port, udp_port,
        "udp-port", data_ok,
        data_listener.bind_error if data_listener is not None else "",
        "bands send sensor frames here"))
    checks.append(_udp_port_check(
        ctx, "port_udp_beacon", "Discovery port (UDP %d)" % announce_port,
        announce_port, "announce-port", beacon_ok,
        getattr(discovery, "announce_error", "") if discovery is not None else "",
        "bands announce themselves here"))
    checks.append(check_lan(ctx))
    checks.append(check_wifi(ssid, redact))
    fwcheck = check_firewall(plat, fwinfo if isinstance(fwinfo, dict) else None,
                             traffic_ok)
    if fwcheck:
        checks.append(fwcheck)
    net = check_internet(got("internet", {}) or {})
    if net:
        checks.append(net)
    lb = results.get("loopback")
    if isinstance(lb, dict) and "_error" in lb:
        lb = _chk("loopback", "Self-test frame", WARN,
                  "The self-test could not run (%s)." % lb["_error"])
    if lb:
        checks.append(lb)

    # ---- the cause
    fw_blocked = False
    if plat == "windows" and isinstance(fwinfo, dict) and fwinfo.get("readable"):
        fw_blocked = not all(fwinfo["ports"].get(p) for p in fw.PORTS)
    listener_bad = (data_ok is False) or (beacon_ok is False)
    if discovery is None and not ctx["running"]:
        # standalone: a busy port that is another OpenMuscle is not a failure
        listener_bad = any(c["id"] in ("port_udp_data", "port_udp_beacon")
                           and c["status"] == FAIL for c in checks)
    facts = {
        "lan_ip_ok": netutil.is_usable_ipv4(ctx["pc_host"]),
        "listener_ok": False if listener_bad else None,
        "firewall_blocked": fw_blocked, "beacon_recent": beacon_recent,
        "data_flowing": data_flowing, "ssid": ssid, "pc_host": ctx["pc_host"],
        "devices": devices,
    }
    cause, sentence, action = infer_cause(facts)

    heard = [d for d in devices if d.get("heard")]
    bands_chk = _bands_check(heard, devices, cause, sentence, action)
    checks.append(bands_chk)

    if ctx.get("temp_discovery") and ctx_disc is not None:
        try:
            ctx_disc.stop()
        except Exception:
            pass

    devices_seen = [{"device_id": d["device_id"], "ip": d["ip"], "age_s": d["age_s"]}
                    for d in heard]
    likely_blocked = bool(fw_blocked and not traffic_ok) or cause == "firewall"
    return {
        "ok": all(c["status"] != FAIL for c in checks),
        "checks": checks,
        "udp_port": udp_port,
        "bound_host": (data_listener.bind_ip if data_listener is not None
                       else "0.0.0.0"),
        "devices_seen": devices_seen,
        "last_announce_s": last_announce_s,
        "firewall": {"platform": plat, "likely_blocked": likely_blocked,
                     "how_to_fix": firewall_advice(
                         plat, fwinfo if isinstance(fwinfo, dict) else None)},
        "likely_cause": cause,
        "advice": {"sentence": sentence, "action": action},
    }


def _bands_check(heard, devices, cause, sentence, action):
    title = "Bands on the network"
    if heard and cause == "none":
        names = ", ".join("%s at %s" % (d["device_id"], d["ip"]) for d in heard)
        return _chk("bands", title, PASS, "Found: %s." % names)
    if cause == "client_isolation" and heard:
        return _chk("bands", title, FAIL, sentence, action)
    return _chk("bands", title, WARN, sentence, action)


# ----- text output -----------------------------------------------------------

_LABEL = {PASS: "PASS", WARN: "WARN", FAIL: "FAIL"}


def format_text(result, header=True):
    """Plain pass/fail lines, one per check, with the fix under each problem."""
    lines = []
    if header:
        lines.append("OpenMuscle doctor")
        lines.append("")
    for c in result["checks"]:
        lines.append("  %s  %s: %s" % (_LABEL[c["status"]], c["title"], c["detail"]))
        if c["status"] != PASS and c["fix"]:
            lines.append("        Fix: %s" % c["fix"])
    lines.append("")
    if result["ok"]:
        bad = [c for c in result["checks"] if c["status"] == WARN]
        lines.append("Everything needed is working."
                     + (" (%d thing%s to look at above.)" % (len(bad),
                                                             "" if len(bad) == 1 else "s")
                        if bad else ""))
    else:
        n = sum(1 for c in result["checks"] if c["status"] == FAIL)
        lines.append("%d problem%s found. Fix the lines marked FAIL, then run "
                     "'openmuscle doctor' again." % (n, "" if n == 1 else "s"))
    return "\n".join(lines)


# ----- reports (no Wi-Fi names, passwords or capture data) -------------------

def versions():
    out = {"python": sys.version.split()[0],
           "platform": _platform.platform(),
           "machine": _platform.machine()}
    for name in ("openmuscle", "fastapi", "uvicorn", "numpy", "pandas",
                 "scikit-learn", "click", "matplotlib", "pydantic"):
        try:
            out[name] = importlib.metadata.version(name)
        except Exception:
            out[name] = "not installed"
    return out


# Log sources whose messages carry take, session, wearer or model names (or URLs
# that contain them). Only the source and level are kept for these in a report.
_NAMEY_SOURCES = ("recording", "recorder", "capture", "session", "meta", "training",
                  "replay", "inference", "uvicorn.access")

_FILE_EXT = r"(?:csv|pkl|npz|labels\.json|meta\.json|json)"


def _folder_pattern(folder):
    """A case-insensitive regex for `folder` that accepts either slash style."""
    parts = [p for p in re.split(r"[\\/]+", folder) if p]
    lead = r"[\\/]+" if folder[:1] in "\\/" else ""
    return re.compile(lead + r"[\\/]+".join(re.escape(p) for p in parts) + r"(?!\w)",
                      re.I)


def scrub(text, ssid=None):
    """Remove what must never leave the machine from free text: the Wi-Fi name,
    anything that looks like a password, wearer and take names, file names of
    recordings and models, and the user's home folder (any case, either slash)."""
    if not text:
        return text
    if ssid:
        text = text.replace(ssid, "<wifi-name>")
    text = re.sub(r"(?i)\b(password|passwd|psk|pass|ssid|token|secret)\b(\s*[=:]\s*)"
                  r"(\"[^\"]*\"|'[^']*'|\S+)", r"\1\2<hidden>", text)
    text = re.sub(r"(?i)(\"(?:password|ssid|psk)\"\s*:\s*)\"[^\"]*\"", r'\1"<hidden>"',
                  text)
    text = re.sub(r"(?i)\b(wearer|subject|participant)(\s*[=:]\s*)[^,)\r\n]*",
                  r"\1\2<hidden>", text)
    text = re.sub(r"(?i)(name adjusted:\s*)'[^']*'\s*->\s*'[^']*'", r"\1<hidden>", text)
    for folder in (str(paths.data_root()), os.path.expanduser("~")):
        if folder and len(folder) > 3:
            text = _folder_pattern(folder).sub("~", text)
    # Any other user profile folder (another drive or account) is masked too.
    text = re.sub(r"(?i)(?:\b[A-Za-z]:)?[\\/]+(?:Users|home)[\\/]+[^\\/\s'\"]+", "~", text)
    # A file name with spaces: everything from the last separator or quote to the
    # extension. Then names without spaces anywhere in the text.
    text = re.sub(r"(?i)(?<=[\\/'\"])[^\\/'\"<>|]*\." + _FILE_EXT + r"\b", "<file>", text)
    text = re.sub(r"(?i)[^\s'\"<>|]+\." + _FILE_EXT + r"\b", "<file>", text)
    return text


def scrub_log_entry(entry, ssid=None):
    """One LogBuffer entry as a single scrubbed line. Entries from sources that
    handle names keep only the source and level; the message body is dropped."""
    stamp = time.strftime("%H:%M:%S", time.localtime(entry.get("t", 0)))
    source = str(entry.get("source", ""))
    message = str(entry.get("message", ""))
    low = source.lower()
    if any(low == n or low.startswith(n + ".") or low.endswith("." + n)
           for n in _NAMEY_SOURCES) or "wearer=" in message.lower():
        message = "(details removed)"
    return scrub("%s %-5s %s: %s" % (stamp, entry.get("level", ""), source, message), ssid)


def report_text(result, ssid=None):
    """The text of a support report: versions plus the doctor results."""
    safe = json.loads(json.dumps(result))
    for c in safe["checks"]:
        c["detail"] = scrub(c["detail"], ssid)
        c["fix"] = scrub(c["fix"], ssid)
    lines = ["OpenMuscle support report",
             "Created: " + time.strftime("%Y-%m-%d %H:%M:%S"),
             "",
             "Versions"]
    for k, v in versions().items():
        lines.append("  %s: %s" % (k, v))
    lines += ["", "Results", format_text(safe, header=False), "",
              "Devices heard: %d" % len(safe["devices_seen"])]
    for d in safe["devices_seen"]:
        lines.append("  %s at %s (%s s ago)" % (d["device_id"], d["ip"], d["age_s"]))
    lines += ["", "Likely cause: %s" % safe["likely_cause"],
              scrub(safe["advice"]["sentence"], ssid),
              "",
              "This report contains no Wi-Fi names, passwords or recordings."]
    return "\n".join(lines) + "\n"


def write_report_file(result, path, ssid=None):
    """`openmuscle doctor --report`: the report as a UTF-8 text file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_text(result, ssid))
    return path


def build_support_zip(result, log_entries=(), ssid=None, out_dir=None):
    """Write openmuscle-support-<time>.zip (report, results JSON, recent logs) into
    the exports folder and return its path. `log_entries` are LogBuffer dicts."""
    out_dir = out_dir or paths.exports_dir()
    os.makedirs(str(out_dir), exist_ok=True)
    name = "openmuscle-support-%s.zip" % time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(str(out_dir), name)
    safe = json.loads(json.dumps(result))
    for c in safe["checks"]:
        c["detail"] = scrub(c["detail"], ssid)
        c["fix"] = scrub(c["fix"], ssid)
    log_lines = [scrub_log_entry(e, ssid) for e in log_entries]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.txt", report_text(result, ssid))
        z.writestr("doctor.json", json.dumps(safe, indent=2))
        z.writestr("versions.json", json.dumps(versions(), indent=2))
        z.writestr("recent-logs.txt", "\n".join(log_lines) + ("\n" if log_lines else ""))
    return path

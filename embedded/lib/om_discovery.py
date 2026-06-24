# om_discovery.py - Wi-Fi discovery for source-role devices.
#
# Two parallel mechanisms per spec section 4:
#   1. mDNS hostname registration (best-effort; uses sta.config(hostname=...)
#      which some MicroPython builds publish as an mDNS A record). The
#      `_openmuscle._udp` service type with TXT records is not registered
#      via this path; the UDP broadcast beacon below covers the gap.
#   2. UDP broadcast beacon to 255.255.255.255:beacon_port (default 3141)
#      carrying the announce JSON. Reliable; works across consumer routers
#      that disable mDNS / multicast.
#
# Cadence: ~1 Hz while no hub is subscribed. Once subscribers.has_any()
# returns True the beacon stops to keep the channel quiet, and resumes
# the moment the subscriber list empties.

import uasyncio as asyncio
import socket
import ujson
import time
import om_logger as log


class Discovery:
    def __init__(self, settings, subscribers, sta,
                 device_type, services, caps,
                 extra_fields=None,
                 beacon_port=3141, announce_interval_s=1,
                 fw_version=None,
                 device_id=None,
                 mdns_service="_openmuscle._udp"):
        """
        settings:     om_settings.Settings instance
        subscribers:  om_subscribers.Subscribers instance
        sta:          network.WLAN(STA_IF), used for hostname + IP lookup
        device_type:  "flexgrid" | "lask5" | "openhand" | ...
        services:     dict mapping capability name -> port
                      e.g. {"label": 3141, "cmd": 8002}
        caps:         list of capability strings, e.g. ["label","status","cmd"]
        extra_fields: optional dict merged into the announce payload
                      (e.g. {"matrix": [15,4]} for FlexGrid)
        beacon_port:  UDP port to broadcast announces to (default 3141)
        device_id:    optional override; defaults to settings["device_id"]
        fw_version:   optional override; defaults to settings.get("fw_version","unknown")
        mdns_service: mDNS service type string for documentation
        """
        self.settings = settings
        self.subscribers = subscribers
        self.sta = sta
        self.device_type = device_type
        self.services = services
        self.caps = caps
        self.extra_fields = extra_fields or {}
        self.beacon_port = beacon_port
        self.announce_interval_s = announce_interval_s
        self.device_id = device_id or settings.get("device_id")
        self.fw_version = fw_version or settings.get("fw_version", "unknown")
        self.mdns_service = mdns_service

        self._beacon_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._beacon_sock.setblocking(False)
        try:
            self._beacon_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception as e:
            log.warn("SO_BROADCAST setsockopt failed: {}".format(e))

        self._mdns_registered = False

    def _announce_payload(self):
        """Build the announce JSON. The IP is NOT in the payload; consumers
        take it from the broadcast packet source or the mDNS A record."""
        pkt = {
            "v":         "1.0",
            "type":      "announce",
            "id":        self.device_id,
            "role":      "source",
            "dev":       self.device_type,
            "fw":        self.fw_version,
            "transports": ["wifi"],  # phase 4 appends "ble"
            "caps":      list(self.caps),
            "services":  dict(self.services),
            "ts":        time.ticks_ms(),
        }
        for k, v in self.extra_fields.items():
            pkt[k] = v
        return pkt

    def register_mdns(self):
        """Best-effort mDNS hostname registration. Some MicroPython builds
        publish the STA hostname as an mDNS A record (so `<device_id>.local`
        resolves); others do not. Either way the broadcast beacon is the
        reliable discovery path."""
        if self._mdns_registered:
            return
        try:
            self.sta.config(hostname=self.device_id)
        except Exception as e:
            log.warn("mDNS hostname set failed (beacon will cover it): {}".format(e))
        self._mdns_registered = True
        log.info("mDNS hostname best-effort registered as {}".format(self.device_id))

    async def announce_loop(self):
        """Periodic broadcast beacon. Goes quiet once at least one hub is
        subscribed; resumes when the subscriber list empties."""
        try:
            self.register_mdns()
        except Exception:
            pass

        while True:
            try:
                if not self.subscribers.has_any():
                    self._send_beacon()
            except Exception as e:
                log.warn("announce_loop iter failed: {}".format(e))
            await asyncio.sleep(self.announce_interval_s)

    def _send_beacon(self):
        try:
            payload = ujson.dumps(self._announce_payload()).encode("utf-8")
            self._beacon_sock.sendto(payload, ("255.255.255.255", self.beacon_port))
        except OSError as e:
            errno = getattr(e, "errno", None) or (e.args[0] if e.args else None)
            # ENOMEM (12) and EAGAIN (11) are non-fatal; next tick retries.
            if errno not in (11, 12):
                log.warn("Beacon send failed: errno={} {}".format(errno, e))
        except Exception as e:
            log.warn("Beacon send unexpected error: {}".format(e))

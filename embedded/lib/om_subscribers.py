# om_subscribers.py - Subscriber list for source-role devices.
#
# A hub (phone, PC) discovers this device via om_discovery, opens the command
# channel exposed by om_commands, and sends a `subscribe` message with its
# (host, port, transport). This module stores the resulting subscriber list,
# fields heartbeats, and prunes entries whose last heartbeat aged past the
# configured timeout (default 5 s per spec section 5.1).
#
# Devices fan out frames to wifi_targets() each scan.

import time
import om_logger as log


class Subscribers:
    def __init__(self, max_subscribers=4, heartbeat_timeout_s=5):
        self.max_subscribers = max_subscribers
        self.heartbeat_timeout_ms = int(heartbeat_timeout_s * 1000)
        # Each entry: dict {host, port, transport, hub_id, last_heartbeat_ms}
        self._entries = []

    def add(self, host, port, transport="wifi", hub_id=None):
        """Add or refresh a subscriber. Returns True if accepted, False if the
        list is full. Refreshing an existing (host, port, transport) tuple
        does not consume a new slot."""
        now = time.ticks_ms()
        for e in self._entries:
            if e["host"] == host and e["port"] == port and e["transport"] == transport:
                e["last_heartbeat_ms"] = now
                if hub_id:
                    e["hub_id"] = hub_id
                log.info("Subscriber refreshed: {} {}:{} (hub_id={})".format(
                    transport, host, port, hub_id))
                return True
        if len(self._entries) >= self.max_subscribers:
            log.warn("Subscriber list full ({}/{}); rejecting {}:{}".format(
                len(self._entries), self.max_subscribers, host, port))
            return False
        self._entries.append({
            "host":              host,
            "port":              port,
            "transport":         transport,
            "hub_id":            hub_id,
            "last_heartbeat_ms": now,
        })
        log.info("Subscriber added: {} {}:{} (hub_id={}, {}/{})".format(
            transport, host, port, hub_id, len(self._entries), self.max_subscribers))
        return True

    def remove(self, host, port, transport="wifi"):
        """Explicit unsubscribe. Returns True if found+removed, False if not."""
        for i, e in enumerate(self._entries):
            if e["host"] == host and e["port"] == port and e["transport"] == transport:
                self._entries.pop(i)
                log.info("Subscriber removed: {} {}:{}".format(transport, host, port))
                return True
        return False

    def heartbeat(self, host, port, transport="wifi"):
        """Refresh a subscriber's heartbeat. If the (host,port,transport) is
        not currently subscribed this is a no-op + warning."""
        now = time.ticks_ms()
        for e in self._entries:
            if e["host"] == host and e["port"] == port and e["transport"] == transport:
                e["last_heartbeat_ms"] = now
                return True
        log.warn("Heartbeat from unknown subscriber {}:{} ({})".format(host, port, transport))
        return False

    def prune_stale(self):
        """Drop subscribers whose last heartbeat is older than the timeout.
        Returns the number of entries dropped."""
        now = time.ticks_ms()
        kept = []
        dropped = 0
        for e in self._entries:
            age_ms = time.ticks_diff(now, e["last_heartbeat_ms"])
            if age_ms > self.heartbeat_timeout_ms:
                log.info("Subscriber stale, dropping: {}:{} age={}ms".format(
                    e["host"], e["port"], age_ms))
                dropped += 1
            else:
                kept.append(e)
        self._entries = kept
        return dropped

    def wifi_targets(self):
        """List of (host, port) for every active Wi-Fi subscriber."""
        return [(e["host"], e["port"]) for e in self._entries if e["transport"] == "wifi"]

    def ble_targets(self):
        """List of hub identifiers for every active BLE subscriber (phase 4)."""
        return [e.get("hub_id") for e in self._entries if e["transport"] == "ble"]

    def count(self):
        return len(self._entries)

    def has_any(self):
        return len(self._entries) > 0

    def snapshot(self):
        """List of subscriber summaries for diagnostics / UI / get_info."""
        now = time.ticks_ms()
        return [
            {
                "host":      e["host"],
                "port":      e["port"],
                "transport": e["transport"],
                "hub_id":    e.get("hub_id"),
                "age_ms":    time.ticks_diff(now, e["last_heartbeat_ms"]),
            }
            for e in self._entries
        ]

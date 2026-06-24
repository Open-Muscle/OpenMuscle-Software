"""Native OpenMuscle V4 discovery + subscribe for the PC hub.

Replaces the interim pc/bridge_subscriber.py: discovers V4 sources and keeps a
TCP subscription (1 Hz heartbeat) to each, so the existing UDP listener receives
their sensor frames with no other change.
"""

from openmuscle.discovery.manager import DiscoveryManager, DiscoveredDevice

__all__ = ["DiscoveryManager", "DiscoveredDevice"]

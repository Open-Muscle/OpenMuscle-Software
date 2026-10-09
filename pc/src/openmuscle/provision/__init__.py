"""Wi-Fi provisioning client for OpenMuscle bands and labelers.

Talks to the small HTTP server a factory-fresh (or reset) device runs on its own
access point (see PROVISIONING.md in the OpenMuscle-Hub repo). Stdlib only. No
UI and no web routes live here; callers (CLI, web app) wrap these functions.
"""

from openmuscle.provision.client import (
    AP_HOST,
    Credentials,
    DeviceInfo,
    ProvisionError,
    ProvisionResult,
    ap_ssid_for,
    get_info,
    probe_ap,
    provision,
    reprovision,
    scan,
    validate_credentials,
    wait_for_device,
)

__all__ = [
    "AP_HOST",
    "Credentials",
    "DeviceInfo",
    "ProvisionError",
    "ProvisionResult",
    "ap_ssid_for",
    "get_info",
    "probe_ap",
    "provision",
    "reprovision",
    "scan",
    "validate_credentials",
    "wait_for_device",
]

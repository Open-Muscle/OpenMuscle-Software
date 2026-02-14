"""Registry of device type handlers for extensible device support."""

from typing import Callable

# Map device_type -> handler function
_handlers: dict[str, Callable] = {}


def register_device(device_type: str):
    """Decorator to register a packet handler for a device type.

    Example:
        @register_device("flexgrid")
        def handle_flexgrid(packet):
            matrix = packet.data["matrix"]
            ...
    """
    def decorator(func):
        _handlers[device_type] = func
        return func
    return decorator


def get_handler(device_type: str) -> Callable | None:
    return _handlers.get(device_type)


def list_devices() -> list[str]:
    return list(_handlers.keys())

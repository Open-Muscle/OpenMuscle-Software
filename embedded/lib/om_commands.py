# om_commands.py - TCP command server for source-role devices.
#
# Hubs connect to this device's cmd_port and send JSON command messages,
# one per line. The server replies with a JSON ack per line. See spec
# section 6.1 for the standard verb taxonomy.
#
# This module provides:
#   - CommandServer: the asyncio TCP server + dispatch loop
#   - build_standard_handlers(state): factory for the verbs that every
#     source device needs (subscribe / unsubscribe / heartbeat / get_info /
#     start_stream / stop_stream / reboot). Device-specific verbs can be
#     added on top by the caller.
#
# `state` is an object the device provides. Required attributes:
#     subscribers       om_subscribers.Subscribers
#     device_id         str
#     device_type       str  ("flexgrid"|"lask5"|...)
#     fw_version        str
#     caps              list[str]
#     extra_info        dict (merged into get_info responses)
#     start_stream()    method
#     stop_stream()     method
#     request_reboot()  method

import uasyncio as asyncio
import ujson
import om_logger as log


_OK  = "ok"
_ERR = "error"


class CommandServer:
    def __init__(self, port, handlers):
        """
        port:     TCP port to bind
        handlers: dict mapping verb -> async fn(data, peer) -> dict
                  Each handler returns the ack's `data` field.
                  Raising any exception turns the ack into an error.
        """
        self.port = port
        self.handlers = handlers
        self._server = None

    async def start(self):
        """Bind and start accepting connections in the background."""
        self._server = await asyncio.start_server(self._handle_client, "0.0.0.0", self.port)
        log.info("Command server listening on TCP {}".format(self.port))

    async def _handle_client(self, reader, writer):
        peer = writer.get_extra_info("peername") or ("?", 0)
        log.info("Command client connected: {}".format(peer))
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                ack = await self._handle_one(line, peer)
                writer.write(ujson.dumps(ack).encode("utf-8") + b"\n")
                await writer.drain()
        except Exception as e:
            log.warn("Command client {} errored: {}".format(peer, e))
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            log.info("Command client disconnected: {}".format(peer))

    async def _handle_one(self, raw_line, peer):
        try:
            pkt = ujson.loads(raw_line.decode("utf-8"))
        except Exception as e:
            return {"v": "1.0", "type": "ack", "status": _ERR,
                    "msg_id": None, "data": {"message": "invalid_json: {}".format(e)}}

        msg_id = pkt.get("msg_id")
        data = pkt.get("data") or {}
        verb = data.get("verb")
        if not verb:
            return {"v": "1.0", "type": "ack", "status": _ERR,
                    "msg_id": msg_id, "data": {"message": "missing verb in data"}}
        handler = self.handlers.get(verb)
        if handler is None:
            return {"v": "1.0", "type": "ack", "status": _ERR,
                    "msg_id": msg_id, "data": {"message": "unknown_verb: " + str(verb)}}

        try:
            ack_data = await handler(data, peer)
        except Exception as e:
            log.warn("Command verb={} from {} raised: {}".format(verb, peer, e))
            return {"v": "1.0", "type": "ack", "status": _ERR,
                    "msg_id": msg_id, "data": {"verb": verb, "message": str(e)}}
        return {"v": "1.0", "type": "ack", "status": _OK,
                "msg_id": msg_id, "data": dict({"verb": verb}, **(ack_data or {}))}


def build_standard_handlers(state):
    """Return a dict of handlers for the verbs every source device needs.
    Devices can extend this dict with their own verbs before passing it
    to CommandServer."""

    async def subscribe(data, peer):
        host = data.get("host") or peer[0]
        port = int(data["port"])
        transport = data.get("transport", "wifi")
        hub_id = data.get("hub_id")
        ok = state.subscribers.add(host, port, transport, hub_id)
        return {
            "accepted":         ok,
            "subscriber_count": state.subscribers.count(),
            "max_subscribers":  state.subscribers.max_subscribers,
        }

    async def unsubscribe(data, peer):
        host = data.get("host") or peer[0]
        port = int(data["port"])
        transport = data.get("transport", "wifi")
        removed = state.subscribers.remove(host, port, transport)
        return {"removed": removed, "subscriber_count": state.subscribers.count()}

    async def heartbeat(data, peer):
        host = data.get("host") or peer[0]
        port = int(data["port"])
        transport = data.get("transport", "wifi")
        refreshed = state.subscribers.heartbeat(host, port, transport)
        return {"refreshed": refreshed}

    async def get_info(data, peer):
        info = {
            "id":          state.device_id,
            "dev":         state.device_type,
            "fw":          state.fw_version,
            "caps":        list(state.caps),
            "subscribers": state.subscribers.snapshot(),
        }
        # Device-specific extras (matrix dims for FlexGrid, piston count for LASK5, etc.)
        for k, v in getattr(state, "extra_info", {}).items():
            info[k] = v
        return info

    async def start_stream(data, peer):
        state.start_stream()
        return {"streaming": True}

    async def stop_stream(data, peer):
        state.stop_stream()
        return {"streaming": False}

    async def reboot(data, peer):
        state.request_reboot()
        return {"rebooting": True}

    return {
        "subscribe":    subscribe,
        "unsubscribe":  unsubscribe,
        "heartbeat":    heartbeat,
        "get_info":     get_info,
        "start_stream": start_stream,
        "stop_stream":  stop_stream,
        "reboot":       reboot,
    }

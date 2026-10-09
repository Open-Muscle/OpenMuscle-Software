"""/ws/webcam accepts only the local machine.

The camera page can only work on http://localhost, so the socket that carries
its hand labels is closed for anyone else: a device elsewhere on the network
(openmuscle web --lan) must not be able to inject labels into a recording, and
a web page from another site must not reach it through the user's browser.

Driven with raw ASGI messages: starlette's TestClient needs httpx, which is not
a dependency of this package.
"""

import asyncio
import json

import pytest

from openmuscle.web.app import (_is_loopback_host, _origin_matches_host,
                                create_app)


def _ws_scope(client_host, headers):
    hdrs = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return {
        "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws",
        "path": "/ws/webcam", "raw_path": b"/ws/webcam", "query_string": b"",
        "root_path": "", "headers": hdrs, "subprotocols": [],
        "client": (client_host, 51234) if client_host else None,
        "server": ("127.0.0.1", 8000),
    }


def _connect(app, client_host, headers):
    """Open the socket, send one frame, return (accepted, sent_messages)."""
    async def run():
        inbox = asyncio.Queue()
        sent = []
        await inbox.put({"type": "websocket.connect"})

        async def receive():
            return await inbox.get()

        async def send(msg):
            sent.append(msg)
            if msg["type"] == "websocket.accept":
                await inbox.put({"type": "websocket.receive",
                                 "text": json.dumps({"device_id": "webcam-left"})})
                await inbox.put({"type": "websocket.disconnect", "code": 1000})

        await app(_ws_scope(client_host, headers), receive, send)
        return sent

    sent = asyncio.run(run())
    return any(m["type"] == "websocket.accept" for m in sent), sent


@pytest.fixture
def app(tmp_path):
    return create_app(udp_port=53977, captures_dir=str(tmp_path))


def test_localhost_page_connects(app):
    ok, _ = _connect(app, "127.0.0.1", {"host": "localhost:8000",
                                        "origin": "http://localhost:8000"})
    assert ok


def test_ipv6_loopback_and_mapped_ipv4_connect(app):
    assert _connect(app, "::1", {"host": "localhost:8000"})[0]
    assert _connect(app, "::ffff:127.0.0.1", {"host": "localhost:8000"})[0]


def test_client_without_origin_header_connects(app):
    # A script or tool on this computer sends no Origin; only browsers do.
    assert _connect(app, "127.0.0.1", {"host": "127.0.0.1:8000"})[0]


def test_non_loopback_client_is_rejected(app):
    ok, sent = _connect(app, "192.168.1.50", {"host": "192.168.1.20:8000",
                                              "origin": "http://192.168.1.20:8000"})
    assert not ok
    assert sent[0]["type"] == "websocket.close"
    assert sent[0]["code"] == 1008


def test_unknown_client_address_is_rejected(app):
    assert not _connect(app, None, {"host": "localhost:8000"})[0]


def test_other_site_origin_is_rejected_even_from_loopback(app):
    ok, _ = _connect(app, "127.0.0.1", {"host": "localhost:8000",
                                        "origin": "https://evil.example"})
    assert not ok


def test_loopback_helper():
    for good in ("127.0.0.1", "127.5.5.5", "::1", "::ffff:127.0.0.1", "[::1]"):
        assert _is_loopback_host(good), good
    for bad in ("192.168.0.2", "10.0.0.1", "::ffff:10.0.0.1", "testclient", "", None,
                "localhost"):
        assert not _is_loopback_host(bad), bad


def test_origin_helper():
    assert _origin_matches_host(None, "localhost:8000")
    assert _origin_matches_host("http://localhost:8000", "localhost:8000")
    assert _origin_matches_host("http://LOCALHOST:8000", "localhost:8000")
    assert not _origin_matches_host("http://localhost:9999", "localhost:8000")
    assert not _origin_matches_host("http://localhost:8000", None)
    assert not _origin_matches_host("null", "localhost:8000")

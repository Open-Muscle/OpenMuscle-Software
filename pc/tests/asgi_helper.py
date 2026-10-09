"""Call a FastAPI app in-process over ASGI (starlette's TestClient needs httpx,
which is not a dependency of this package)."""

import asyncio
import json


def call(app, method, path, query="", body=None, client=("127.0.0.1", 50000)):
    """-> (status, headers dict with lower-case keys, body bytes)"""
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    headers = [(b"host", b"127.0.0.1:8000")]
    if body is not None:
        headers += [(b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode())]
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query.encode(), "root_path": "", "headers": headers,
        "client": client, "server": ("127.0.0.1", 8000),
    }

    async def run():
        sent = {"status": None, "headers": {}, "body": b""}
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await asyncio.sleep(3600)

        async def send(message):
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]
                sent["headers"] = {k.decode().lower(): v.decode()
                                   for k, v in message.get("headers", [])}
            elif message["type"] == "http.response.body":
                sent["body"] += message.get("body", b"")

        await app(scope, receive, send)
        return sent["status"], sent["headers"], sent["body"]

    return asyncio.run(run())


def call_json(app, *args, **kwargs):
    status, _headers, body = call(app, *args, **kwargs)
    try:
        return status, (json.loads(body.decode("utf-8")) if body else None)
    except ValueError:
        return status, body

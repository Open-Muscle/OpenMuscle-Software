"""Small network helpers shared by the simulators.

The web server is HTTPS whenever a mkcert pair is configured (it auto-loads
one from ~/.openmuscle), and a plain `ws://` client cannot talk to it. These
helpers let the simulators follow whatever the server speaks: probe the port
for TLS, build the matching ws/wss and http/https URLs, and accept the dev
server's self-signed or mkcert certificate (a simulator only ever sends
synthetic data, so skipping verification leaks nothing).
"""

import json
import socket
import ssl
import urllib.error
import urllib.request


def insecure_ssl_context() -> ssl.SSLContext:
    """TLS client context that does not verify the server certificate.

    The simulator targets the user's own dev server, usually through a
    locally generated certificate the Python process does not trust.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def detect_web_scheme(ip: str, port: int, timeout: float = 2.0) -> str:
    """Return "https" if the port completes a TLS handshake, else "http".

    A plain-HTTP server answers a ClientHello with garbage or a reset, which
    shows up here as an SSL error, so any failure falls back to "http" and the
    caller's own connect reports the real problem (server down, wrong port).
    """
    try:
        with socket.create_connection((ip, port), timeout=timeout) as raw:
            with insecure_ssl_context().wrap_socket(raw, server_hostname=ip):
                return "https"
    except (OSError, ssl.SSLError):
        return "http"


def resolve_scheme(scheme: str, ip: str, port: int) -> str:
    """Normalize a user-facing scheme ("auto", "http", "https") to http/https."""
    scheme = (scheme or "auto").strip().lower()
    if scheme == "auto":
        return detect_web_scheme(ip, port)
    if scheme not in ("http", "https"):
        raise ValueError("web scheme must be auto, http or https, got %r" % scheme)
    return scheme


def ws_url(scheme: str, ip: str, port: int, path: str) -> str:
    """ws:// or wss:// URL matching an http/https scheme."""
    return "%s://%s:%d%s" % ("wss" if scheme == "https" else "ws", ip, port, path)


def ws_connect(scheme: str, ip: str, port: int, path: str):
    """Open a sync websockets client to the web server, TLS-aware.

    Returns the connection (a context manager). Raises on failure; callers
    print the error rather than retrying silently.
    """
    from websockets.sync.client import connect
    url = ws_url(scheme, ip, port, path)
    if scheme == "https":
        return connect(url, ssl=insecure_ssl_context(), open_timeout=5)
    return connect(url, open_timeout=5)


def http_json(scheme: str, ip: str, port: int, path: str, body=None,
              method=None, timeout: float = 3.0):
    """GET (or POST when body is given) a JSON endpoint on the web server.

    Returns the decoded JSON. Raises OSError / urllib errors on failure.
    """
    url = "%s://%s:%d%s" % (scheme, ip, port, path)
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if body is not None else "GET"))
    ctx = insecure_ssl_context() if scheme == "https" else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise OSError("HTTP %d from %s %s %s" % (e.code, req.get_method(), url, detail))

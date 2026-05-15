"""In-memory log ring buffer + Python `logging` bridge for the web UI.

Why this exists
---------------
FastAPI / uvicorn write logs to stderr. Our own server code uses `print()`
or scattered ad-hoc messages. None of that is visible to an operator who's
just looking at the browser. When something goes wrong (training fails,
a packet won't parse, a device drops off) the user has no breadcrumbs.

This module gives the web UI a Logs panel. Every Python `logging` record
(uvicorn access + errors, FastAPI exceptions, our explicit `om.log` calls)
is captured into a deque and exposed through the snapshot/REST. The buffer
is bounded so a long-running session can't OOM the server.

Pattern: call `install(buffer)` once at startup, then use the standard
`logging` module everywhere. Code that wants to log without importing
logging can call `buffer.event(level, source, message)` directly.
"""

import logging
import threading
import time
from collections import deque


class LogBuffer:
    """Bounded ring buffer of log entries, safe to read from any thread.

    Each entry: {id: int, t: float (unix seconds), level: str, source: str,
                 message: str}. `id` is monotonic so the frontend can
    poll with ?since=<last_id> and only get new entries.
    """

    def __init__(self, capacity: int = 300):
        self.capacity = capacity
        self._log: deque = deque(maxlen=capacity)
        self._seq = 0
        self._lock = threading.Lock()

    def event(self, level: str, source: str, message: str) -> dict:
        """Append a log entry. Thread-safe. Returns the entry."""
        with self._lock:
            self._seq += 1
            entry = {
                "id": self._seq,
                "t": time.time(),
                "level": level,
                "source": source,
                "message": message,
            }
            self._log.append(entry)
        return entry

    def info(self, source: str, message: str) -> dict:
        return self.event("INFO", source, message)

    def warn(self, source: str, message: str) -> dict:
        return self.event("WARN", source, message)

    def error(self, source: str, message: str) -> dict:
        return self.event("ERROR", source, message)

    def entries(self, since_id: int = 0, limit: int = 200) -> list:
        """Return entries with id > since_id, newest at the end. Capped to
        `limit` (most recent kept when truncating)."""
        with self._lock:
            out = [e for e in self._log if e["id"] > since_id]
        if len(out) > limit:
            out = out[-limit:]
        return out

    def latest_id(self) -> int:
        with self._lock:
            return self._seq


class _LogBufferHandler(logging.Handler):
    """logging.Handler shim that pushes records into a LogBuffer.

    Inherits filtering / level rules from the standard `logging` framework
    so a single handler captures uvicorn.access, uvicorn.error, fastapi,
    and any application module that uses logging.getLogger(__name__).
    """

    def __init__(self, buffer: LogBuffer):
        super().__init__()
        self.buffer = buffer

    def emit(self, record: logging.LogRecord):
        try:
            msg = record.getMessage()
            # Some log calls pass exception info; tack on a brief tail.
            if record.exc_info:
                msg = "{} | exc={}".format(msg, record.exc_info[1])
            self.buffer.event(
                level=record.levelname,
                source=record.name,
                message=msg,
            )
        except Exception:
            # Logging must never raise into the application path. If we
            # can't format a record for any reason, just drop it silently.
            pass


def install(buffer: LogBuffer, level: int = logging.INFO) -> _LogBufferHandler:
    """Attach a buffer handler to the root logger.

    Catches uvicorn + FastAPI + every named module logger by virtue of the
    root logger being the ancestor of all of them. Idempotent: re-calling
    just attaches another handler (deduping is the caller's problem).
    """
    handler = _LogBufferHandler(buffer)
    handler.setLevel(level)
    # We don't bother with format strings -- the buffer entry has level +
    # source + message as structured fields.
    root = logging.getLogger()
    # Make sure the root threshold is low enough to actually let records
    # through; the handler's own level filters again.
    if root.level == logging.WARNING or root.level == 0:
        root.setLevel(level)
    root.addHandler(handler)
    return handler

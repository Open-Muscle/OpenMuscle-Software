"""Direct pyserial uploader for the LASK5 device.

mpremote keeps hitting "could not enter raw REPL" because the running labeler
floods the UART. This script speaks the MicroPython raw-REPL protocol over
COM24 directly: Ctrl-C to interrupt, Ctrl-A to enter raw REPL, write each
file by sending Python code that creates it, Ctrl-B to exit, then reset.

Run from the embedded/ folder so the relative paths to lib/ and devices/
resolve correctly.
"""
import os
import sys
import time
import serial

PORT = "COM24"
BAUD = 115200

# (local_path, remote_path) tuples to push
PUSH_LIST = [
    ("lib/om_network.py",          "/lib/om_network.py"),
    ("lib/om_subscribers.py",      "/lib/om_subscribers.py"),
    ("lib/om_discovery.py",        "/lib/om_discovery.py"),
    ("lib/om_commands.py",         "/lib/om_commands.py"),
    ("lib/om_provisioning.py",     "/lib/om_provisioning.py"),
    ("devices/lask5_v2/labeler.py", "/labeler.py"),
]


def drain(s, ms=200):
    """Read everything currently buffered on the serial port."""
    end = time.time() + ms / 1000.0
    buf = bytearray()
    while time.time() < end:
        b = s.read(s.in_waiting or 1)
        if b:
            buf.extend(b)
        else:
            time.sleep(0.01)
    return bytes(buf)


def enter_raw_repl(s):
    """Halt any running script and enter raw REPL mode."""
    # Two Ctrl-C: halt any running script
    s.write(b"\r\x03\x03")
    time.sleep(0.3)
    drain(s, 300)
    # Ctrl-A: enter raw REPL
    s.write(b"\r\x01")
    time.sleep(0.5)
    resp = drain(s, 500)
    if b"raw REPL" not in resp:
        # Try one more time after a fresh interrupt
        s.write(b"\x03\x03\r\x01")
        time.sleep(0.7)
        resp = drain(s, 700)
        if b"raw REPL" not in resp:
            raise RuntimeError("Could not enter raw REPL. Got: {!r}".format(resp[:200]))
    print("  raw REPL active")


def exit_raw_repl(s):
    s.write(b"\r\x02")
    time.sleep(0.2)
    drain(s, 200)


def exec_raw(s, code):
    """Execute Python code in raw REPL; return (stdout, stderr) bytes."""
    s.write(code.encode("utf-8"))
    s.write(b"\x04")  # EOT: run
    # Expect b"OK" + output + 0x04 + error + 0x04>
    buf = bytearray()
    end = time.time() + 8.0
    while time.time() < end:
        b = s.read(s.in_waiting or 1)
        if b:
            buf.extend(b)
            if buf.count(b"\x04") >= 2 and buf.endswith(b"\x04>"):
                break
        else:
            time.sleep(0.01)
    raw = bytes(buf)
    if raw[:2] != b"OK":
        raise RuntimeError("raw REPL did not OK: {!r}".format(raw[:80]))
    # Strip leading OK, trailing >
    body = raw[2:]
    if body.endswith(b"\x04>"):
        body = body[:-2]
    parts = body.split(b"\x04")
    out = parts[0] if len(parts) >= 1 else b""
    err = parts[1] if len(parts) >= 2 else b""
    return out, err


def ensure_dir(s, remote_path):
    """If remote_path is in a subdirectory, mkdir the parent."""
    parts = remote_path.lstrip("/").split("/")
    if len(parts) <= 1:
        return
    d = "/" + "/".join(parts[:-1])
    code = (
        "import uos\n"
        "try:\n"
        "    uos.stat('{0}')\n"
        "except OSError:\n"
        "    uos.mkdir('{0}')\n"
        "print('dir ok')\n"
    ).format(d)
    out, err = exec_raw(s, code)
    if err.strip():
        raise RuntimeError("mkdir failed for {}: {!r}".format(d, err[:120]))


def push_file(s, local_path, remote_path):
    """Upload local file to remote path via raw REPL."""
    with open(local_path, "rb") as f:
        data = f.read()
    print("  pushing {} -> {} ({} bytes)".format(local_path, remote_path, len(data)))
    ensure_dir(s, remote_path)
    # Open file on device for writing
    open_code = "f=open('{}','wb')\n".format(remote_path)
    out, err = exec_raw(s, open_code)
    if err.strip():
        raise RuntimeError("open() failed: {!r}".format(err[:120]))
    # Write in chunks. Each chunk goes through raw REPL exec; small enough
    # to fit comfortably under the stdin buffer (typ. ~1KB safe).
    chunk = 256
    for i in range(0, len(data), chunk):
        piece = data[i:i+chunk]
        # Encode as repr() of bytes so embedded backslashes / newlines work
        code = "f.write({!r})\n".format(piece)
        out, err = exec_raw(s, code)
        if err.strip():
            raise RuntimeError("write() failed at offset {}: {!r}".format(i, err[:120]))
    out, err = exec_raw(s, "f.close()\nimport uos\nprint(uos.stat('{}')[6])\n".format(remote_path))
    if err.strip():
        raise RuntimeError("close()/stat() failed: {!r}".format(err[:120]))
    written = int(out.strip())
    if written != len(data):
        raise RuntimeError("size mismatch: wrote {} but stat says {}".format(len(data), written))
    print("    confirmed {} bytes on device".format(written))


def hard_reset(s):
    print("  triggering machine.reset()...")
    s.write(b"\r\x01")  # re-enter raw REPL just in case
    time.sleep(0.3)
    drain(s, 200)
    s.write(b"import machine\nmachine.reset()\n\x04")
    time.sleep(0.5)


def main():
    s = serial.Serial(PORT, BAUD, timeout=2)
    print("Opened {}".format(PORT))
    try:
        enter_raw_repl(s)
        for local, remote in PUSH_LIST:
            push_file(s, local, remote)
        hard_reset(s)
        print("Done; device is rebooting")
    finally:
        s.close()


if __name__ == "__main__":
    main()

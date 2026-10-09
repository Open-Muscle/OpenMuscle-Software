"""Keep the computer awake while OpenMuscle runs.

A recording that runs while the laptop idles to sleep loses the Wi-Fi link to
the bands and the take is cut short. While the server runs we ask the OS not to
sleep because of inactivity:

  Windows  SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED), held by a
           small thread so it lasts until stop() (or the process ends). The
           screen may still turn off. `powercfg /requests` lists python under
           SYSTEM while it is held.
  macOS    `caffeinate -i -w <pid>` when caffeinate exists; it exits by itself
           when this process does.
  Linux    nothing (no portable, permission-free way); a no-op.

Closing the lid still puts the computer to sleep on most laptops; that is the
power setting "When I close the lid" in the OS and is not overridden here.
"""

import shutil
import subprocess
import sys
import threading

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _kernel32():
    """The kernel32 handle (a function so tests can stub ctypes)."""
    import ctypes
    return ctypes.windll.kernel32


class KeepAwake:
    """start() asks the OS to stay awake, stop() lets go. Both are idempotent and
    never raise; `active` says whether the request is currently held and
    `method` names how ("windows", "caffeinate", or "" when nothing was done)."""

    def __init__(self, platform=None):
        self.platform = platform if platform is not None else sys.platform
        self.active = False
        self.method = ""
        self._proc = None
        self._thread = None
        self._release = threading.Event()
        self._held = threading.Event()

    def start(self):
        if self.active:
            return True
        try:
            if self.platform.startswith("win"):
                self.active = self._start_windows()
            elif self.platform == "darwin":
                self.active = self._start_caffeinate()
        except Exception:
            self.active = False
        if not self.active:
            self.method = ""
        return self.active

    def stop(self):
        if not self.active:
            return
        self.active = False
        try:
            if self.method == "windows":
                self._release.set()
                if self._thread is not None:
                    self._thread.join(2.0)
                self._thread = None
            elif self.method == "caffeinate" and self._proc is not None:
                self._proc.terminate()
                self._proc = None
        except Exception:
            pass
        self.method = ""

    # ---- Windows -----------------------------------------------------------

    def _start_windows(self):
        k32 = _kernel32()
        self._release.clear()
        self._held.clear()
        ok = {"v": False}

        def _hold():
            # The state belongs to the calling thread, so this thread sets it,
            # waits, and clears it; the server's own threads are untouched.
            try:
                ok["v"] = bool(k32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
            except Exception:
                ok["v"] = False
            self._held.set()
            if ok["v"]:
                self._release.wait()
                try:
                    k32.SetThreadExecutionState(ES_CONTINUOUS)
                except Exception:
                    pass

        self._thread = threading.Thread(target=_hold, name="om-keep-awake",
                                        daemon=True)
        self._thread.start()
        self._held.wait(2.0)
        if ok["v"]:
            self.method = "windows"
        return ok["v"]

    # ---- macOS -------------------------------------------------------------

    def _start_caffeinate(self):
        exe = shutil.which("caffeinate")
        if not exe:
            return False
        import os
        self._proc = subprocess.Popen(
            [exe, "-i", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        self.method = "caffeinate"
        return True

# display_lask5.py - LASK5-specific display rendering
#
# Taskbar with battery level, joystick indicator, and piston bar chart,
# plus an optional boot splash animation (24 frames, ported from the
# monolithic firmware).

import time


def play_splash(display, frame_delay_ms=100):
    """Play the OM-LASK5 boot splash if the frame data and an OLED are both
    available. Safe no-op otherwise.

    The animation is written by blitting raw bytearrays directly into the
    SSD1306 frame buffer, which matches how the monolithic firmware did it.
    If `splash_frames.py` isn't on the device (e.g. tight flash budget), we
    skip silently so boot still succeeds.

    Args:
        display: om_display.Display instance.
        frame_delay_ms: per-frame delay; ~100ms gives ~2.4s total at 24 frames.
    """
    if not getattr(display, "available", False):
        return
    try:
        import splash_frames
    except ImportError:
        return

    oled = display.oled
    if oled is None or not hasattr(oled, "buffer"):
        return

    try:
        for frame in splash_frames.frames:
            try:
                oled.buffer[:] = frame
                oled.show()
            except Exception:
                # If a frame is the wrong length for this OLED size, abort the
                # animation rather than crashing the boot.
                return
            time.sleep_ms(frame_delay_ms)
    finally:
        # The 24 frames are ~12 KB of bytearrays + the .py overhead. After the
        # splash plays we never need them again -- and on ESP32-S3 the internal
        # heap (where lwip lives) is small enough that hanging on to this data
        # caused [Errno 12] ENOMEM on UDP sends. Free it back to GC.
        import sys, gc
        if "splash_frames" in sys.modules:
            del sys.modules["splash_frames"]
        gc.collect()


def draw_taskbar(display, sensor_values, mins, maxes,
                 joystick_x=None, joystick_y=None, battery_pct=None,
                 mode=None):
    """
    Render the LASK5 taskbar on a 128x32 OLED.

    Args:
        display: Display instance
        sensor_values: list of 4 raw ADC readings
        mins: list of 4 calibration minimums
        maxes: list of 4 calibration maximums
        joystick_x: raw joystick X ADC value (optional)
        joystick_y: raw joystick Y ADC value (optional)
        battery_pct: battery percentage string (optional)
        mode: current stream mode (e.g. "udp" / "espnow") shown in the
              header so the operator can tell at a glance where packets
              are flowing. None = no mode label.
    """
    if not display.available:
        return

    display.fill(0)

    # Header: "OM-LASK5 [MODE] [BATT]". Mode is shortened to fit ("ESPN" for
    # espnow) so the joystick indicator at x=87 still has room.
    label = "OM-LASK5"
    if mode:
        short = {"udp": "UDP", "espnow": "EPN"}.get(mode, mode[:3].upper())
        label = "{} {}".format(label, short)
    if battery_pct:
        label = "{} {}".format(label, battery_pct)
    display.text(label, 0, 0)

    # Joystick indicator (top-right area)
    if joystick_x is not None and joystick_y is not None:
        joy_center = 2048
        joy_cx, joy_cy = 107, 6
        ox = -int((joystick_x - joy_center) / 500)
        oy = -int((joystick_y - joy_center) / 500)
        dx = max(87, min(joy_cx + ox, 127))
        dy = max(0, min(joy_cy + oy, 14))
        display.fill_rect(dx, dy, 3, 3, 1)

    # Piston bar chart (bottom-right area)
    x, y = 87, 17
    display.fill_rect(x, y, 40, 14, 1)
    display.fill_rect(x + 1, y + 1, 38, 12, 0)

    for i in range(min(4, len(sensor_values))):
        div_top = sensor_values[i] - mins[i]
        div_bottom = maxes[i] - mins[i]
        if div_bottom == 0:
            div_bottom = 1
        ch = int((div_top / div_bottom) * 12)
        ch = max(0, min(12, ch))
        r_x = ((i + 1) * 7) + x
        r_y = 13 - ch + y
        display.fill_rect(r_x, r_y, 5, ch, 1)

        # Channel labels (left side)
        display.text(str(i + 1), i * 20, 16)
        display.text(str(ch * 8), i * 20, 24)

    display.show()

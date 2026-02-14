# display_lask5.py - LASK5-specific display rendering
#
# Taskbar with battery level, joystick indicator, and piston bar chart.

def draw_taskbar(display, sensor_values, mins, maxes,
                 joystick_x=None, joystick_y=None, battery_pct=None):
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
    """
    if not display.available:
        return

    display.fill(0)

    # Header
    label = "OM-LASK5"
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

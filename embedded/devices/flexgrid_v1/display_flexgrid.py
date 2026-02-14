# display_flexgrid.py - FlexGrid-specific display rendering
#
# Heatmap visualization of the 16x4 sensor matrix on the SSD1306 OLED.

def draw_sensor_matrix(display, matrix):
    """
    Render a pressure heatmap on the OLED.
    Pressure levels are shown as progressively larger filled rectangles.

    Args:
        display: Display instance from om_display
        matrix: list of lists from SensorMatrix.scan_matrix()
    """
    if not display.available:
        return

    display.fill(0)
    cell = 7
    cols = len(matrix)
    rows = len(matrix[0]) if cols else 0
    x_off = (display.width - cols * cell) // 2
    y_off = (display.height - rows * cell) // 2

    for c in range(cols):
        for r in range(rows):
            v = matrix[c][r]
            x = x_off + c * cell
            y = y_off + r * cell
            if v < 200:
                continue
            elif v < 1000:
                display.pixel(x + 3, y + 3, 1)
            elif v < 2000:
                display.fill_rect(x + 2, y + 2, 3, 3, 1)
            elif v < 3000:
                display.fill_rect(x + 1, y + 1, 5, 5, 1)
            else:
                display.fill_rect(x, y, cell, cell, 1)

    display.show()


def draw_menu(display, state):
    """Render a menu state dict on the OLED."""
    if not display.available:
        return

    display.fill(0)
    mode = state.get("mode", "")
    if mode:
        display.text(mode, 0, 0)

    menu_items = state.get("menu_items", [])
    sel = state.get("current_selection", 0)
    for idx, item in enumerate(menu_items):
        prefix = ">" if idx == sel else " "
        display.text("{}{}".format(prefix, item), 0, (idx + 1) * 8)

    display.show()

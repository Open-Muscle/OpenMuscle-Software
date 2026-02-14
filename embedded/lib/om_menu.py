# om_menu.py - Button-driven menu system for OpenMuscle devices
#
# Provides a reusable menu framework with scroll support and debouncing.
# Devices define their own menu trees and action handlers.

from machine import Pin
import time
import om_logger as log

class MenuManager:
    def __init__(self, display, select_pin=10, menu_pin=21, debounce_ms=200):
        """
        Args:
            display: Display instance for rendering
            select_pin: GPIO for select/confirm button (active LOW)
            menu_pin: GPIO for menu/next button (active LOW)
            debounce_ms: debounce delay in milliseconds
        """
        self.display = display
        self.select_btn = Pin(select_pin, Pin.IN, Pin.PULL_UP)
        self.menu_btn = Pin(menu_pin, Pin.IN, Pin.PULL_UP)
        self.debounce = debounce_ms

        # Menu state
        self.menus = []
        self.current_menu = 0
        self.current_selection = 0

    def set_menus(self, menus):
        """
        Set the menu structure.

        Args:
            menus: list of lists of strings, e.g.:
                [["Start", "Settings", "About"], ["WiFi", "UDP", "Back"]]
        """
        self.menus = menus
        self.current_menu = 0
        self.current_selection = 0

    def _debounced(self, pin):
        if pin.value() == 0:
            time.sleep_ms(self.debounce)
            while pin.value() == 0:
                pass
            return True
        return False

    def check_buttons(self):
        """Poll buttons and update menu state. Returns (menu_idx, selection_idx) if changed."""
        if self._debounced(self.menu_btn):
            self.current_menu = (self.current_menu + 1) % len(self.menus)
            self.current_selection = 0

        if self._debounced(self.select_btn):
            menu_items = self.menus[self.current_menu]
            self.current_selection = (self.current_selection + 1) % len(menu_items)

    def get_state(self):
        """Return current menu state as a dict for display rendering."""
        if not self.menus:
            return {"mode": "No Menu", "menu_items": [], "current_selection": 0}
        return {
            "mode": "Menu {}".format(self.current_menu),
            "menu_items": self.menus[self.current_menu],
            "current_selection": self.current_selection,
        }

    def render(self):
        """Render current menu state to the display."""
        if not self.display.available or not self.menus:
            return
        state = self.get_state()
        self.display.fill(0)
        sel = state["current_selection"]
        for idx, item in enumerate(state["menu_items"]):
            prefix = ">" if idx == sel else " "
            self.display.text("{}{}".format(prefix, item), 0, idx * 8)
        self.display.show()

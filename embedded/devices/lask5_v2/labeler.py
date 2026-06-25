# labeler.py - LASK5 V2 main application
#
# 4-finger target value acquirer with joystick, ESPNOW + WiFi UDP.
# Uses the shared OpenMuscle library.
#
# Design notes:
#   - UDP streaming runs continuously from boot (background asyncio task).
#     This differs from the monolithic firmware where streaming was menu-gated
#     under [0] UDP Send. Continuous matches FlexGrid behaviour and makes the
#     `openmuscle web` UI plug-and-play.
#   - The on-OLED menu is for occasional actions (recalibrate, show device
#     info). Opening it does NOT pause streaming.
#   - `values` in the UDP packet are calibrated 0.0..1.0 floats. Raw averaged
#     ADCs are used for the on-OLED bar chart and the Calibrate flow.

import time
import machine
from machine import Pin, ADC
import uasyncio as asyncio
import om_logger as log
from om_device import BaseDevice
from om_subscribers import Subscribers
from om_discovery import Discovery
from om_commands import CommandServer, build_standard_handlers
from sensor_pistons import SensorPistons
from display_lask5 import draw_taskbar, play_splash


class LASK5(BaseDevice):
    DEVICE_TYPE = "lask5"
    DEVICE_DEFAULTS = {
        "device_id": "lask5-01",
        "wifi_ssid": "OpenMuscle",
        "wifi_password": "3141592653",
        "udp_target_ip": "192.168.1.48",
        "udp_port": 3141,
        "scl_pin": 9,
        "sda_pin": 8,
        "oled_width": 128,
        "oled_height": 32,
        "oled_flip": True,
        "mins": [0, 0, 0, 0],
        "maxes": [2500, 2500, 2500, 2500],
        "led_pin": 15,
        "start_pin": 11,
        "select_pin": 10,
        "up_pin": 41,
        "down_pin": 42,
        "joystick_x_pin": 6,
        "joystick_y_pin": 5,
        "joystick_sw_pin": 7,
        "sample_rate_hz": 25,
        # Streaming mode: "udp" feeds the openmuscle web UI / labeler PC;
        # "espnow" broadcasts directly to a paired bracelet/robot hand.
        # Persisted in settings.json -- can be toggled live from the menu.
        "stream_mode": "udp",
        # Discovery + subscribe protocol additions (see spec section 4-6).
        # No udp_target_ip is used for sensor frames in this mode; sources
        # unicast to subscribed hubs only. ESP-NOW path is unchanged.
        "fw_version":           "v3.0.0",
        "cmd_port":             8002,
        "udp_announce_port":    3140,   # broadcast announce port (PROTOCOL.md v1.0 port split)
        "udp_sensor_port":      3141,   # unicast data port for sensor/label frames
        "announce_interval_s":  1,
        "max_subscribers":      4,
        "heartbeat_timeout_s":  5,
    }

    # Menu UX: a single "Start" button both OPENS the menu (from live) and
    # CYCLES selection (within menu). Some LASK5 hardware revs don't have
    # up/down buttons, so we can't rely on them for navigation. Select
    # invokes; up/down still work where present (back-compat). To exit the
    # menu without doing anything, cycle to "Back" and Select.
    #
    # "Mode" is at index 0 so the most-frequently-toggled action is one
    # button press away. The Mode item shows the *current* value
    # ("Mode: UDP" / "Mode: ESPNOW") and selecting it cycles in place
    # without leaving the menu.
    MENU_ITEMS = ("Mode", "Calibrate", "About", "Back")

    def __init__(self):
        super().__init__()

        # Calibration: load from settings up front so the sensor returns
        # normalized 0..1 values from the very first packet. Source of truth
        # for mins/maxes lives on the sensor instance; the Calibrate menu
        # updates it via `sensor.set_calibration()` so display + wire stay in
        # sync without us mirroring state here.
        self.sensor = SensorPistons(
            mins=self.settings.get("mins", [0, 0, 0, 0]),
            maxes=self.settings.get("maxes", [2500, 2500, 2500, 2500]),
        )

        # Buttons (active-LOW, pull-up)
        self.start_btn = Pin(self.settings.get("start_pin", 11), Pin.IN, Pin.PULL_UP)
        self.select_btn = Pin(self.settings.get("select_pin", 10), Pin.IN, Pin.PULL_UP)
        self.up_btn = Pin(self.settings.get("up_pin", 41), Pin.IN, Pin.PULL_UP)
        self.down_btn = Pin(self.settings.get("down_pin", 42), Pin.IN, Pin.PULL_UP)

        # Joystick
        jx_pin = self.settings.get("joystick_x_pin", 6)
        jy_pin = self.settings.get("joystick_y_pin", 5)
        self.joystick_x = ADC(Pin(jx_pin))
        self.joystick_x.atten(ADC.ATTN_11DB)
        self.joystick_y = ADC(Pin(jy_pin))
        self.joystick_y.atten(ADC.ATTN_11DB)

        # LED
        self.led = Pin(self.settings.get("led_pin", 15), Pin.OUT)

        # ESPNOW peer (broadcast by default)
        self.peer = b'\xff\xff\xff\xff\xff\xff'

        # Display ownership: when the menu / calibrate / about routines are
        # active, _display_loop yields and lets them write the OLED directly.
        self._display_owner = "live"  # "live" | "menu" | "modal"

        # Discovery + subscribe state (V3 protocol):
        #   subscribers - hubs currently receiving label frames
        #   _streaming  - false halts UDP send_loop without removing subscribers
        #   _reboot_requested - flag polled by reboot_watcher
        # Discovery + CommandServer instances are built in run() after the
        # network is up and the Wi-Fi STA has an IP.
        self.subscribers = Subscribers(
            max_subscribers=self.settings.get("max_subscribers", 4),
            heartbeat_timeout_s=self.settings.get("heartbeat_timeout_s", 5),
        )
        self._streaming = True
        self._reboot_requested = False

    # ----- DeviceState interface for om_commands.build_standard_handlers -----
    # The standard handler factory needs: subscribers, device_id, device_type,
    # fw_version, caps, extra_info, start_stream(), stop_stream(), request_reboot()

    @property
    def fw_version(self):
        return self.settings.get("fw_version", "v3.0.0")

    @property
    def device_type(self):
        return self.DEVICE_TYPE

    @property
    def caps(self):
        return ["label", "status", "cmd"]

    @property
    def extra_info(self):
        return {"pistons": 4, "joystick": True, "espnow_paired": True}

    def start_stream(self):
        self._streaming = True
        log.info("Streaming started")

    def stop_stream(self):
        self._streaming = False
        log.info("Streaming stopped")

    def request_reboot(self):
        self._reboot_requested = True
        log.info("Reboot requested")

    # ----- helpers -----

    def blink(self, count=2, on_ms=300, off_ms=200):
        for _ in range(count):
            self.led.value(1)
            time.sleep_ms(on_ms)
            self.led.value(0)
            time.sleep_ms(off_ms)

    async def _await_release(self, btn, poll_ms=20):
        """Block until `btn` is released. Cheap edge-detect debouncer."""
        while btn.value() == 0:
            await asyncio.sleep_ms(poll_ms)

    async def start(self):
        """Override BaseDevice.start to play the splash before WiFi connect."""
        # Splash first (no network required); skips silently if frames missing.
        play_splash(self.display)
        # Then the standard connect-then-run lifecycle.
        await super().start()

    async def run(self):
        self.blink(2)
        self.network.init_espnow()

        # New in V3 firmware: discovery + subscribe protocol on Wi-Fi.
        # ESP-NOW path to OpenHand is untouched. The cmd server binds before
        # we spawn any other task so hubs hitting the device immediately
        # after boot can connect.
        self._discovery = Discovery(
            self.settings, self.subscribers, self.network.sta,
            device_type=self.DEVICE_TYPE,
            services={
                "label": self.settings.get("udp_sensor_port", 3141),
                "cmd":   self.settings.get("cmd_port", 8002),
            },
            caps=self.caps,
            extra_fields={"pistons": 4, "joystick": True},
            beacon_port=self.settings.get("udp_announce_port", 3140),
            announce_interval_s=self.settings.get("announce_interval_s", 1),
            fw_version=self.fw_version,
            device_id=self.device_id,
        )
        # Cmd server runs under a supervisor (serve_forever) so an asyncio
        # interruption (e.g. mpremote-induced KeyboardInterrupt) cannot
        # silently kill the listener. See PROTOCOL.md section 10.
        self._cmd_server = CommandServer(
            port=self.settings.get("cmd_port", 8002),
            handlers=build_standard_handlers(self),
        )

        # Streaming + display + menu + discovery + subscriber pruning + reboot
        # watcher all run concurrently. UDP fans out to subscribers and ESPNow
        # broadcasts to the paired bracelet in parallel; both kept independent
        # so a Wi-Fi flap does not disturb the ESP-NOW link.
        asyncio.create_task(self._cmd_server.serve_forever())
        asyncio.create_task(self._send_loop())
        asyncio.create_task(self._espnow_loop())
        asyncio.create_task(self._display_loop())
        asyncio.create_task(self._menu_loop())
        asyncio.create_task(self._discovery.announce_loop())
        asyncio.create_task(self._subscriber_prune_loop())
        asyncio.create_task(self._reboot_watcher())

        while True:
            await asyncio.sleep(1)

    async def _subscriber_prune_loop(self):
        """Drop subscribers whose last heartbeat aged past the configured
        timeout. Self-cleans the list when a hub disappears."""
        while True:
            try:
                dropped = self.subscribers.prune_stale()
                if dropped:
                    log.info("Pruned {} stale subscriber(s); remaining={}".format(
                        dropped, self.subscribers.count()))
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                log.warn("subscriber_prune_loop failed: {} ({})".format(
                    type(e).__name__, e))
            await asyncio.sleep(1)

    async def _reboot_watcher(self):
        """Polls the reboot flag set by a `reboot` command verb."""
        while True:
            try:
                if self._reboot_requested:
                    log.info("Soft-resetting in 500 ms...")
                    await asyncio.sleep_ms(500)
                    machine.reset()
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                log.warn("reboot_watcher failed: {} ({})".format(
                    type(e).__name__, e))
            await asyncio.sleep_ms(200)

    # ----- streaming -----

    def _stream_mode(self):
        return self.settings.get("stream_mode", "udp")

    async def _send_loop(self):
        """UDP send loop. Only transmits when stream_mode == 'udp' AND at
        least one hub is subscribed. Keeps reading sensors regardless so
        the on-OLED taskbar stays responsive immediately after a mode toggle.

        V3 protocol change: replaces send_udp() to a hardcoded udp_target_ip
        with send_udp_to_subscribers() that fans out to every entry in the
        live subscriber list. The list is populated by hubs talking to the
        cmd server (see om_commands).

        BaseException catch: matches the FlexGridV4 sensor_loop hardening
        (firmware board #0166). A BaseException (MemoryError, KeyboardInterrupt
        from a dev tool, or asyncio internals raising one of these) used to
        propagate past `except Exception` and silently kill this task; once
        dead, label frames stopped flowing to subscribed hubs and the device
        looked like a black hole until rebooted. CancelledError still
        re-raises so deliberate shutdown works.
        """
        interval = 1.0 / self.settings.get("sample_rate_hz", 25)
        while True:
            try:
                if self._stream_mode() == "udp" and self._streaming:
                    data = self.sensor.read()  # {"values": [0..1, ...]}
                    data["joystick"] = {
                        "x": self.joystick_x.read(),
                        "y": self.joystick_y.read(),
                    }
                    packet = self.make_packet(data)
                    # send_udp_to_subscribers no-ops when the list is empty, so
                    # the work above is wasted when idle; we still do it so the
                    # very first frame after a subscribe is ready to go.
                    await self.network.send_udp_to_subscribers(packet, self.subscribers)
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                log.warn("send_loop iter failed: {} ({})".format(
                    type(e).__name__, e))
            await asyncio.sleep(interval)

    async def _espnow_loop(self):
        """ESPNow broadcast loop. Only transmits when stream_mode == 'espnow'.

        Wire format matches the monolithic firmware's `ESPNowSend()`:
        a string repr of a 5-element list `[p1, p2, p3, p4, jx]` where each
        piston is calibrated to 0..1 then scaled to 0..800 (int), and jx is
        the raw joystick X ADC scaled to 0..800. This keeps existing
        bracelet receivers compatible with no firmware changes on their end.

        BaseException catch matches _send_loop; an espnow_send hardware
        glitch should not silently kill the loop and starve the paired
        OpenHand motor.
        """
        interval = 1.0 / self.settings.get("sample_rate_hz", 25)
        while True:
            try:
                if self._stream_mode() == "espnow":
                    vals = self.sensor.read_calibrated()
                    scaled = [int(v * 800) for v in vals]
                    joy_x_raw = self.joystick_x.read()
                    scaled.append(int((joy_x_raw / 4095.0) * 800))
                    self.network.espnow_send(self.peer, str(scaled))
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                log.warn("espnow_loop iter failed: {} ({})".format(
                    type(e).__name__, e))
            await asyncio.sleep(interval)

    # ----- display -----

    async def _display_loop(self):
        """Renders the live taskbar at ~10 Hz. Yields the OLED when a menu or
        modal owns the display."""
        while True:
            try:
                if self._display_owner == "live":
                    values = self.sensor.read_raw()
                    draw_taskbar(
                        self.display,
                        values,
                        self.sensor.mins,
                        self.sensor.maxes,
                        joystick_x=self.joystick_x.read(),
                        joystick_y=self.joystick_y.read(),
                        mode=self._stream_mode(),
                    )
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                log.warn("display_loop iter failed: {} ({})".format(
                    type(e).__name__, e))
            await asyncio.sleep(0.1)

    # ----- menu -----

    def _menu_label(self, item):
        """Resolve a MENU_ITEMS token to its display string. Dynamic items
        (like Mode) include their current value."""
        if item == "Mode":
            return "Mode:" + self._stream_mode().upper()
        return item

    def _render_menu(self, selection):
        if not self.display.available:
            return
        self.display.fill(0)
        # 128x32 OLED fits 4 lines of 8px text. We use all four for items
        # (no "Menu" header) so Mode/Calibrate/About/Back are visible at once.
        for i, item in enumerate(self.MENU_ITEMS):
            prefix = ">" if i == selection else " "
            self.display.text(
                "{}{}".format(prefix, self._menu_label(item)),
                0, i * 8,
            )
        self.display.show()

    async def _menu_loop(self):
        """Button polling loop.

        Live -> Menu:  press Start.
        Menu navigation: Start advances selection (also Down if available).
                         Up moves backwards (if available).
        Invoke item:   Select.
        Exit menu:     cycle to 'Back' and Select.

        Start does double-duty (open + advance) because some LASK5 hardware
        revs don't have up/down buttons; we can't require them for a
        usable menu.
        """
        idx = 0
        while True:
            if self._display_owner == "live":
                if self.start_btn.value() == 0:
                    await self._await_release(self.start_btn)
                    self._display_owner = "menu"
                    idx = 0
                    self._render_menu(idx)
                await asyncio.sleep_ms(40)
                continue

            if self._display_owner == "menu":
                # Start OR Down -> advance. Up -> backwards. Select -> invoke.
                if self.start_btn.value() == 0 or self.down_btn.value() == 0:
                    btn = self.start_btn if self.start_btn.value() == 0 else self.down_btn
                    await self._await_release(btn)
                    idx = (idx + 1) % len(self.MENU_ITEMS)
                    self._render_menu(idx)
                elif self.up_btn.value() == 0:
                    await self._await_release(self.up_btn)
                    idx = (idx - 1) % len(self.MENU_ITEMS)
                    self._render_menu(idx)
                elif self.select_btn.value() == 0:
                    await self._await_release(self.select_btn)
                    item = self.MENU_ITEMS[idx]
                    # "Mode" cycles in place without leaving the menu so the
                    # user can see the new value land immediately. All other
                    # items invoke a modal action and return to live.
                    if item == "Mode":
                        self._cycle_stream_mode()
                        self._render_menu(idx)
                    else:
                        await self._invoke(item)
                        self._display_owner = "live"
                await asyncio.sleep_ms(40)
                continue

            # "modal" -- the action owns everything; just wait.
            await asyncio.sleep_ms(40)

    def _cycle_stream_mode(self):
        cur = self._stream_mode()
        new = "espnow" if cur == "udp" else "udp"
        self.settings["stream_mode"] = new
        self.settings.save()
        log.info("Stream mode -> " + new)

    async def _invoke(self, item):
        self._display_owner = "modal"
        try:
            if item == "Calibrate":
                await self._run_calibrate()
            elif item == "About":
                await self._show_about()
            elif item == "Back":
                pass  # just return to live (menu_loop handles the transition)
        except Exception as e:
            log.error("Menu action '{}' failed: {}".format(item, e))

    async def _run_calibrate(self):
        """Two-step interactive calibration. Uses asyncio.sleep so the send
        loop keeps pushing packets during the prompts (those 0..1 outputs
        will be wrong for a few seconds while the user is mid-press, which
        is fine -- the operator just initiated calibration, they're not
        expecting valid data right now)."""
        def _prompt(line1, line2=""):
            if self.display.available:
                self.display.fill(0)
                self.display.text(line1, 0, 0)
                if line2:
                    self.display.text(line2, 0, 12)
                self.display.show()
            log.info(line1 + (" " + line2 if line2 else ""))

        _prompt("Calibrate:", "release pistons")
        await asyncio.sleep(2)
        maxes = self.sensor.record_maxes()
        log.info("Maxes recorded: {}".format(maxes))
        _prompt("Maxes:", str(maxes))
        await asyncio.sleep(1)

        _prompt("Calibrate:", "press pistons")
        await asyncio.sleep(3)
        mins = self.sensor.record_mins()
        log.info("Mins recorded: {}".format(mins))
        _prompt("Mins:", str(mins))
        await asyncio.sleep(1)

        self.sensor.save_calibration(self.settings)
        log.info("Calibration saved")
        _prompt("Calibration", "saved")
        await asyncio.sleep(1)

    async def _show_about(self):
        if not self.display.available:
            await asyncio.sleep(2)
            return
        ip = self.network.get_ip() or "no wifi"
        self.display.fill(0)
        self.display.text("OM-LASK5", 0, 0)
        self.display.text(str(self.device_id), 0, 8)
        self.display.text(str(ip), 0, 16)
        self.display.text("v0.3.0 modular", 0, 24)
        self.display.show()
        # Show for a few seconds, then drop back to live.
        await asyncio.sleep(3)

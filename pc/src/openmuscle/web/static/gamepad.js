// OpenMuscle dashboard gamepad labeler.
//
// A USB game controller is a browser-only input (navigator.getGamepads); it
// can't speak UDP. So, exactly like the Quest hand stream, the dashboard reads
// the pad here and pushes axis/button frames over /ws/gamepad, where the server
// synthesizes a device_type="gamepad" label device (ingest_gamepad_packet).
// The controller then behaves like any other label source: pick "Controller" in
// the Labels select, tag a band, hit Record (Tory's ask: plug in a controller
// and capture its inputs as labels).
//
// Rendering: Tory asked for "a virtual controller ... to mimic the look of a
// controller with joysticks and buttons". When the pad reports the Gamepad API
// "standard" mapping (Xbox pads in Chrome/Edge do) with the full standard shape
// (>= 4 axes and >= 16 buttons), we draw an Xbox-style face as inline SVG:
// sticks that translate with the axes, ABXY diamond, d-pad cross, bumpers, and
// analog trigger gauges. Anything else (odd HOTAS, wheel, remapped pad) falls
// back to the original generic panel (axes as bipolar bars, buttons as a lit
// grid) so no device ever renders blank.
//
// Standard mapping indices (w3c gamepad spec, what the code below assumes):
//   buttons: 0 A, 1 B, 2 X, 3 Y, 4 LB, 5 RB, 6 LT, 7 RT, 8 Back, 9 Start,
//            10 L-stick click, 11 R-stick click, 12 up, 13 down, 14 left,
//            15 right, 16 Guide (optional, absent on some pads)
//   axes:    0/1 left stick x/y, 2/3 right stick x/y (up is negative y)
//
// Perf contract: the page already runs a 5Hz WS tick plus this rAF loop, so the
// SVG (or the bars panel) is built ONCE per shape change; every rAF frame only
// mutates transforms / fills / opacity on cached element refs. No per-frame
// innerHTML, no per-frame arrays or objects beyond the frames we already build
// for the WS send. Plain script (no module) so it loads without the importmap;
// exposes window.OMGamepad for app.js to read.

(function () {
    'use strict';

    const SEND_HZ = 50;               // frame rate to the server (steady, low jitter)
    const SEND_INTERVAL = 1000 / SEND_HZ;
    const RECONNECT_MS = 1500;

    // Virtual-controller tuning (viewBox units, viewBox is 400x210).
    const STICK_TRAVEL = 10;          // max cap translation at full deflection (the visual clamp radius)
    const TRIG_H = 32;                // trigger gauge inner fill height at value 1.0
    const TRIG_BOTTOM = 39;           // y of the gauge inner bottom edge (fill grows upward from here)
    const PRESS_THRESH = 0.5;         // analog value above this counts as a hard press (lit state)
    const LIT_MIN = 0.18;             // resting fill-opacity so unpressed controls stay visible

    let ws = null;
    let wantOpen = false;
    let lastSend = 0;
    let panelEl = null;
    let panelShape = '';              // rebuild the panel only when mode or axis/button counts change
    let renderMode = '';              // 'xbox' or 'bars', chosen at rebuild time
    let axisEls = [];                 // bars mode refs
    let btnEls = [];
    let xboxRefs = null;              // xbox mode refs, cached at rebuild time

    const state = {
        connected: false,
        id: '',
        deviceId: '',
        axisCount: 0,
        buttonCount: 0,
    };
    window.OMGamepad = state;

    function wsURL(path) {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        return `${proto}://${location.host}${path}`;
    }

    function connect() {
        if (!wantOpen) return;
        try {
            ws = new WebSocket(wsURL('/ws/gamepad'));
        } catch (e) {
            setTimeout(connect, RECONNECT_MS);
            return;
        }
        ws.onclose = () => { ws = null; if (wantOpen) setTimeout(connect, RECONNECT_MS); };
        ws.onerror = () => { try { ws.close(); } catch (e) {} };
    }

    // First connected pad (index order). Chrome/Edge expose pads only after a
    // button press on that pad, which is why "press a button" is the hint.
    function firstPad() {
        const pads = navigator.getGamepads ? navigator.getGamepads() : [];
        for (let i = 0; i < pads.length; i++) {
            if (pads[i] && pads[i].connected) return pads[i];
        }
        return null;
    }

    // All virtual-controller styling lives here (not styles.css) because that
    // file is being edited concurrently; injecting our own <style> keeps this
    // feature self-contained. Colors lean on the page's theme variables with
    // hex fallbacks. ABXY use the Xbox colorways, muted to match the theme
    // (A green, B red, X blue via --accent, Y yellow).
    function injectStyle() {
        if (document.getElementById('gamepad-style')) return;
        const el = document.createElement('style');
        el.id = 'gamepad-style';
        el.textContent =
            '#gamepad-panel .gp-xbox{display:block;width:100%;height:auto;max-height:260px;margin-top:6px;' +
            '--gp-a:var(--green,#4caf78);--gp-b:#d05a5a;--gp-x:var(--accent,#58a6ff);--gp-y:#c9a23f;}' +
            '#gamepad-panel .gp-x-body{fill:var(--bg-soft,#161b22);stroke:var(--border,#30363d);stroke-width:1.5;}' +
            '#gamepad-panel .gp-x-well{fill:var(--border,#30363d);fill-opacity:.35;stroke:var(--border,#30363d);stroke-width:1;}' +
            '#gamepad-panel .gp-x-cap{fill:var(--fg-dim,#8b949e);fill-opacity:.5;stroke:var(--border,#30363d);stroke-width:1.5;}' +
            '#gamepad-panel .gp-x-cap.on{fill:var(--accent,#58a6ff);fill-opacity:.85;stroke:var(--accent,#58a6ff);stroke-width:2.5;}' +
            '#gamepad-panel .gp-x-face{stroke-width:1.5;}' +
            '#gamepad-panel .gp-x-face.on{stroke-width:3;}' +
            '#gamepad-panel .gp-x-face-label{fill:var(--fg,#e6edf3);font-size:11px;font-weight:600;font-family:inherit;text-anchor:middle;pointer-events:none;}' +
            '#gamepad-panel .gp-x-arm{fill:var(--fg-dim,#8b949e);stroke:var(--border,#30363d);stroke-width:1;}' +
            '#gamepad-panel .gp-x-arm.on{fill:var(--accent,#58a6ff);stroke:var(--accent,#58a6ff);}' +
            '#gamepad-panel .gp-x-arm-center{fill:var(--fg-dim,#8b949e);fill-opacity:.18;}' +
            '#gamepad-panel .gp-x-pill{fill:var(--fg-dim,#8b949e);stroke:var(--border,#30363d);stroke-width:1;}' +
            '#gamepad-panel .gp-x-pill.on{fill:var(--accent,#58a6ff);stroke:var(--accent,#58a6ff);}' +
            '#gamepad-panel .gp-x-trig-track{fill:var(--border,#30363d);fill-opacity:.35;stroke:var(--border,#30363d);stroke-width:1;}' +
            '#gamepad-panel .gp-x-trig-fill{fill:var(--orange,#f0883e);}' +
            '#gamepad-panel .gp-x-small-label{fill:var(--fg-dim,#8b949e);font-size:9px;font-weight:500;font-family:inherit;}';
        document.head.appendChild(el);
    }

    function headHTML(nAxes, nButtons) {
        return `<div class="gp-head"><span class="gp-name"></span>` +
            `<span class="gp-shape">${nAxes} axes · ${nButtons} buttons</span></div>`;
    }

    // Fallback renderer: the original generic panel. Kept intact for pads that
    // do not report the standard mapping (or report a nonstandard shape), so
    // any recognizable input still shows up somewhere.
    function buildBarsPanel(nAxes, nButtons) {
        const axRows = [];
        for (let i = 0; i < nAxes; i++) {
            axRows.push(
                `<div class="gp-axis"><span class="gp-axis-name">A${i}</span>` +
                `<div class="gp-axis-track"><div class="gp-axis-fill" data-ax="${i}"></div></div></div>`);
        }
        let btnCells = '';
        for (let i = 0; i < nButtons; i++) {
            btnCells += `<div class="gp-btn" data-btn="${i}">${i}</div>`;
        }
        panelEl.innerHTML =
            headHTML(nAxes, nButtons) +
            `<div class="gp-axes">${axRows.join('')}</div>` +
            `<div class="gp-btns">${btnCells}</div>`;
        axisEls = Array.from(panelEl.querySelectorAll('.gp-axis-fill'));
        btnEls = Array.from(panelEl.querySelectorAll('.gp-btn'));
    }

    // Xbox-style virtual controller, built once per rebuild. Geometry is fixed
    // in a 400x210 viewBox so the SVG scales with the comparator column width.
    // Element lookup keys (data-gpx): "b<i>" lights with button i, "t<i>" is
    // the analog fill for trigger button i, "s<i>" is stick group i (0 left,
    // 1 right), "sc<i>" is the stick cap lit by click button i.
    function buildXboxPanel(nAxes, nButtons) {
        panelEl.innerHTML =
            headHTML(nAxes, nButtons) +
            `<svg class="gp-xbox" viewBox="0 0 400 210" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">` +
            // Triggers (6/7): analog gauges above the bumpers; the fill rect is
            // bottom-anchored and its height tracks the button value each frame.
            `<rect class="gp-x-trig-track" x="62" y="5" width="16" height="35" rx="3"/>` +
            `<rect class="gp-x-trig-fill" data-gpx="t6" x="64" y="39" width="12" height="0" rx="2"/>` +
            `<text class="gp-x-small-label" x="84" y="26">LT</text>` +
            `<rect class="gp-x-trig-track" x="322" y="5" width="16" height="35" rx="3"/>` +
            `<rect class="gp-x-trig-fill" data-gpx="t7" x="324" y="39" width="12" height="0" rx="2"/>` +
            `<text class="gp-x-small-label" x="316" y="26" text-anchor="end">RT</text>` +
            // Bumpers LB/RB (4/5).
            `<rect class="gp-x-pill" data-gpx="b4" x="40" y="46" width="78" height="14" rx="7"/>` +
            `<text class="gp-x-small-label" x="79" y="56" text-anchor="middle">LB</text>` +
            `<rect class="gp-x-pill" data-gpx="b5" x="282" y="46" width="78" height="14" rx="7"/>` +
            `<text class="gp-x-small-label" x="321" y="56" text-anchor="middle">RB</text>` +
            // Controller body.
            `<rect class="gp-x-body" x="20" y="66" width="360" height="136" rx="44"/>` +
            // Guide (16, optional on some pads), Back (8), Start (9).
            `<circle class="gp-x-pill" data-gpx="b16" cx="200" cy="92" r="11"/>` +
            `<rect class="gp-x-pill" data-gpx="b8" x="158" y="117" width="22" height="10" rx="5"/>` +
            `<rect class="gp-x-pill" data-gpx="b9" x="220" y="117" width="22" height="10" rx="5"/>` +
            // Left stick (axes 0/1, click 10): the cap group translates with
            // the axes, clamped to STICK_TRAVEL viewBox px.
            `<circle class="gp-x-well" cx="88" cy="118" r="27"/>` +
            `<g data-gpx="s0"><circle class="gp-x-cap" data-gpx="sc10" cx="88" cy="118" r="15"/></g>` +
            // Right stick (axes 2/3, click 11).
            `<circle class="gp-x-well" cx="248" cy="166" r="24"/>` +
            `<g data-gpx="s1"><circle class="gp-x-cap" data-gpx="sc11" cx="248" cy="166" r="13"/></g>` +
            // Face buttons in the standard diamond (0 A, 1 B, 2 X, 3 Y).
            `<circle class="gp-x-face" data-gpx="b3" cx="312" cy="95" r="11.5" style="fill:var(--gp-y);stroke:var(--gp-y)"/>` +
            `<text class="gp-x-face-label" x="312" y="99">Y</text>` +
            `<circle class="gp-x-face" data-gpx="b2" cx="289" cy="118" r="11.5" style="fill:var(--gp-x);stroke:var(--gp-x)"/>` +
            `<text class="gp-x-face-label" x="289" y="122">X</text>` +
            `<circle class="gp-x-face" data-gpx="b1" cx="335" cy="118" r="11.5" style="fill:var(--gp-b);stroke:var(--gp-b)"/>` +
            `<text class="gp-x-face-label" x="335" y="122">B</text>` +
            `<circle class="gp-x-face" data-gpx="b0" cx="312" cy="141" r="11.5" style="fill:var(--gp-a);stroke:var(--gp-a)"/>` +
            `<text class="gp-x-face-label" x="312" y="145">A</text>` +
            // D-pad cross (12 up, 13 down, 14 left, 15 right); arms meet a
            // static center square so a lit arm reads cleanly.
            `<rect class="gp-x-arm-center" x="143" y="157" width="18" height="18"/>` +
            `<rect class="gp-x-arm" data-gpx="b12" x="143" y="138" width="18" height="19" rx="2"/>` +
            `<rect class="gp-x-arm" data-gpx="b13" x="143" y="175" width="18" height="19" rx="2"/>` +
            `<rect class="gp-x-arm" data-gpx="b14" x="124" y="157" width="19" height="18" rx="2"/>` +
            `<rect class="gp-x-arm" data-gpx="b15" x="161" y="157" width="19" height="18" rx="2"/>` +
            `</svg>`;

        // Cache every element the rAF loop touches; paintXbox never queries.
        const q = sel => panelEl.querySelector(sel);
        const lit = [];
        // Opacity-lit controls: ABXY, bumpers, back/start/guide, d-pad arms.
        const litIdx = [0, 1, 2, 3, 4, 5, 8, 9, 12, 13, 14, 15, 16];
        for (let i = 0; i < litIdx.length; i++) {
            const el = q(`[data-gpx="b${litIdx[i]}"]`);
            if (el) lit.push({ el, idx: litIdx[i] });
        }
        xboxRefs = {
            caps: [q('[data-gpx="s0"]'), q('[data-gpx="s1"]')],
            capCircles: [q('[data-gpx="sc10"]'), q('[data-gpx="sc11"]')],
            trigFills: [q('[data-gpx="t6"]'), q('[data-gpx="t7"]')],
            lit,
        };
    }

    // Mode choice: the Gamepad API only guarantees the index layout above when
    // mapping === 'standard', and the visual assumes at least the standard
    // shape (4 axes, 16 buttons). Anything else gets the generic bars+grid.
    function ensurePanel(mapping, nAxes, nButtons) {
        panelEl = panelEl || document.getElementById('gamepad-panel');
        if (!panelEl) return;
        const standard = mapping === 'standard' && nAxes >= 4 && nButtons >= 16;
        const shape = `${standard ? 'std' : 'raw'}:${nAxes}x${nButtons}`;
        if (shape === panelShape) return;
        panelShape = shape;
        injectStyle();
        axisEls = [];
        btnEls = [];
        xboxRefs = null;
        renderMode = standard ? 'xbox' : 'bars';
        if (standard) buildXboxPanel(nAxes, nButtons);
        else buildBarsPanel(nAxes, nButtons);
        const nameEl = panelEl.querySelector('.gp-name');
        if (nameEl) nameEl.textContent = state.id || 'controller';
    }

    function clamp1(v) {
        return v > 1 ? 1 : (v < -1 ? -1 : v);
    }

    function paintBars(axes, buttons) {
        // Axes are bipolar (-1..1): grow the fill from the center to the left or
        // right so a resting stick reads as centered, not half-full.
        for (let i = 0; i < axisEls.length; i++) {
            const v = clamp1(axes[i] || 0);
            const el = axisEls[i];
            const half = Math.abs(v) * 50;
            el.style.left = v >= 0 ? '50%' : (50 - half) + '%';
            el.style.width = half + '%';
            el.classList.toggle('neg', v < 0);
        }
        for (let i = 0; i < btnEls.length; i++) {
            const v = buttons[i] || 0;
            const el = btnEls[i];
            el.style.opacity = (0.25 + 0.75 * v).toFixed(2);
            el.classList.toggle('on', v > 0.5);
        }
    }

    function paintXbox(axes, buttons) {
        const r = xboxRefs;
        if (!r) return;
        // Sticks: translate the cap with the axis pair, clamped so full
        // deflection stays inside the well; light the cap on stick click.
        for (let i = 0; i < 2; i++) {
            const dx = clamp1(axes[i * 2] || 0) * STICK_TRAVEL;
            const dy = clamp1(axes[i * 2 + 1] || 0) * STICK_TRAVEL;
            r.caps[i].setAttribute('transform', 'translate(' + dx.toFixed(1) + ' ' + dy.toFixed(1) + ')');
            r.capCircles[i].classList.toggle('on', (buttons[10 + i] || 0) > PRESS_THRESH);
        }
        // Triggers are analog: fill height follows the 0..1 value so a half
        // pull visibly fills the gauge halfway.
        for (let i = 0; i < 2; i++) {
            const h = (buttons[6 + i] || 0) * TRIG_H;
            const el = r.trigFills[i];
            el.setAttribute('height', h.toFixed(1));
            el.setAttribute('y', (TRIG_BOTTOM - h).toFixed(1));
        }
        // Everything else: analog value drives fill-opacity (some pads report
        // pressure on face buttons), with a threshold for the hard-lit state.
        for (let i = 0; i < r.lit.length; i++) {
            const item = r.lit[i];
            const v = buttons[item.idx] || 0;
            item.el.setAttribute('fill-opacity', (LIT_MIN + (1 - LIT_MIN) * v).toFixed(2));
            item.el.classList.toggle('on', v > PRESS_THRESH);
        }
    }

    function paintPanel(axes, buttons) {
        if (renderMode === 'xbox') paintXbox(axes, buttons);
        else paintBars(axes, buttons);
    }

    function setComparatorFlag(on) {
        const comp = document.querySelector('.comparator');
        if (comp) comp.classList.toggle('has-gamepad', on);
    }

    function tick(now) {
        requestAnimationFrame(tick);
        const pad = firstPad();
        if (!pad) {
            if (state.connected) {
                state.connected = false;
                if (panelEl) panelEl.classList.remove('active');
                setComparatorFlag(false);
            }
            return;
        }
        const axes = Array.from(pad.axes, v => +(+v).toFixed(4));
        const buttons = Array.from(pad.buttons, b => +(+b.value).toFixed(4));
        if (!state.connected || state.deviceId !== `gamepad-${pad.index}`) {
            state.connected = true;
            state.id = pad.id || 'controller';
            state.deviceId = `gamepad-${pad.index}`;
            state.axisCount = axes.length;
            state.buttonCount = buttons.length;
        }
        ensurePanel(pad.mapping, axes.length, buttons.length);
        if (panelEl) { panelEl.classList.add('active'); paintPanel(axes, buttons); }
        setComparatorFlag(true);

        // Throttle the network send; the panel repaints every frame regardless.
        if (now - lastSend >= SEND_INTERVAL && ws && ws.readyState === WebSocket.OPEN) {
            lastSend = now;
            ws.send(JSON.stringify({
                device_id: state.deviceId,
                ts: Math.round(now),
                id: state.id,
                axes,
                buttons,
                // Relay the Gamepad API mapping so the server can surface it in
                // the /ws/live snapshot; the OBS /stream view uses it to pick the
                // standard xbox face vs the generic bars fallback (matches this
                // dashboard's own render). Nothing else in read/send changes.
                mapping: pad.mapping,
            }));
        }
    }

    function start() {
        wantOpen = true;
        connect();
        requestAnimationFrame(tick);
    }

    window.addEventListener('gamepadconnected', () => { if (!ws) connect(); });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();

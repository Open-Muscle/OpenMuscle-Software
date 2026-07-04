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
// Also renders a live input panel (axes as bipolar bars, buttons as a lit grid)
// into #gamepad-panel if present, so you can SEE the inputs being captured (a
// natural surface for a recording livestream). Plain script (no module) so it
// loads without the importmap; exposes window.OMGamepad for app.js to read.

(function () {
    'use strict';

    const SEND_HZ = 50;               // frame rate to the server (steady, low jitter)
    const SEND_INTERVAL = 1000 / SEND_HZ;
    const RECONNECT_MS = 1500;

    let ws = null;
    let wantOpen = false;
    let lastSend = 0;
    let panelEl = null;
    let panelShape = '';              // rebuild the panel only when axis/button counts change
    let axisEls = [];
    let btnEls = [];

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

    function ensurePanel(nAxes, nButtons) {
        panelEl = panelEl || document.getElementById('gamepad-panel');
        if (!panelEl) return;
        const shape = `${nAxes}x${nButtons}`;
        if (shape === panelShape) return;
        panelShape = shape;
        axisEls = [];
        btnEls = [];
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
            `<div class="gp-head"><span class="gp-name"></span>` +
            `<span class="gp-shape">${nAxes} axes · ${nButtons} buttons</span></div>` +
            `<div class="gp-axes">${axRows.join('')}</div>` +
            `<div class="gp-btns">${btnCells}</div>`;
        axisEls = Array.from(panelEl.querySelectorAll('.gp-axis-fill'));
        btnEls = Array.from(panelEl.querySelectorAll('.gp-btn'));
        const nameEl = panelEl.querySelector('.gp-name');
        if (nameEl) nameEl.textContent = state.id || 'controller';
    }

    function paintPanel(axes, buttons) {
        // Axes are bipolar (-1..1): grow the fill from the center to the left or
        // right so a resting stick reads as centered, not half-full.
        for (let i = 0; i < axisEls.length; i++) {
            const v = Math.max(-1, Math.min(1, axes[i] || 0));
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
        ensurePanel(axes.length, buttons.length);
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

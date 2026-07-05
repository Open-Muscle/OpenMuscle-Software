// OpenMuscle OBS stream view (Tory's play-a-game-and-capture session).
//
// DISPLAY ONLY. This page connects to /ws/live and renders ENTIRELY from the
// server snapshot: the two band heatmaps, a big FRAMES CAPTURED counter, and a
// virtual controller. It is captured as a window (or browser source) in OBS
// while Tory plays a game wearing the forearm bracelets.
//
// HARD ARCHITECTURE RULE: this file MUST NOT call navigator.getGamepads and
// MUST NOT open /ws/gamepad. The main dashboard is the sole controller reader;
// it streams the pad to the server, which relays it in the snapshot. A second
// reader here would double-send label frames and corrupt the recording. The
// controller below is drawn purely from the snapshot gamepad device's
// axes/buttons/mapping (reconstructed from its flat `values` + counts).
//
// CONTROLLER-RENDER PATH: a COMPACT standalone renderer lives here (not a
// refactor of gamepad.js). gamepad.js's SVG renderer is an IIFE tightly bound
// to its own module-level panel state and the local pad; lifting it into a
// shared display fn risked the working dashboard controller. So we redraw the
// same standard-mapping geometry from snapshot values (sticks axes 0/1 + 2/3,
// ABXY buttons 0-3, triggers 6/7 analog, dpad 12-15, bumpers 4/5), with a bars
// fallback for non-standard pads. The heatmap ramp IS shared (OMHeatColor) so
// heatmap colors can't drift from the dashboard.
//
// Plain script (no module) so it loads without the importmap.

(function () {
    'use strict';

    const RECONNECT_MS = 1500;

    // Standard-mapping visual tuning, mirrored from gamepad.js so the /stream
    // controller reads like the dashboard's (same clamp radius, thresholds).
    const STICK_TRAVEL = 10;    // max cap translation (viewBox px) at full deflection
    const TRIG_H = 32;          // trigger gauge fill height at value 1.0
    const TRIG_BOTTOM = 39;     // y of gauge inner bottom edge (fill grows upward)
    const PRESS_THRESH = 0.5;   // analog value above this = hard-lit
    const LIT_MIN = 0.18;       // resting fill-opacity so idle controls stay visible

    // Heatmap ramp: shared module (loaded before us) with a local fallback so a
    // missing heatmap-color.js never blanks the hero. Fallback is byte-identical
    // to the shared stops (see heatmap-color.js / app.js pressureColor).
    function pressureColor(v, vmax) {
        if (window.OMHeatColor) return window.OMHeatColor.pressureColor(v, vmax);
        if (v < 8) return '#1a1f2b';
        const t = Math.max(0, Math.min(1, v / vmax));
        const stops = [
            [40, 45, 90], [85, 40, 140], [165, 45, 140],
            [225, 90, 90], [255, 165, 60], [255, 230, 90],
        ];
        const seg = Math.min(stops.length - 2, Math.floor(t * (stops.length - 1)));
        const localT = (t * (stops.length - 1)) - seg;
        const a = stops[seg], b = stops[seg + 1];
        const lerp = (x, y) => Math.round(x + (y - x) * localT);
        return `rgb(${lerp(a[0], b[0])},${lerp(a[1], b[1])},${lerp(a[2], b[2])})`;
    }

    // Match the dashboard's sticky shared vmax behavior (never auto-shrinks) so
    // the two views scale colors identically across a session.
    let heatmapVmax = 2000;

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function clamp1(v) { return v > 1 ? 1 : (v < -1 ? -1 : v); }

    // ?transparent=1 -> transparent page bg for an OBS browser-source overlay.
    if (new URLSearchParams(location.search).get('transparent') === '1') {
        document.body.classList.add('transparent');
    }

    // ---- DOM refs ----
    const bandsEl = document.getElementById('bands');
    const connEl = document.getElementById('conn');
    const counterEl = document.getElementById('counter');
    const recDotEl = document.getElementById('rec-dot');
    const counterTitleEl = document.getElementById('counter-title');
    const counterNumEl = document.getElementById('counter-num');
    const counterSubEl = document.getElementById('counter-sub');
    const counterBandsEl = document.getElementById('counter-bands');
    const padNameEl = document.getElementById('pad-name');
    const padBodyEl = document.getElementById('pad-body');

    // ---------- band heatmaps ----------
    //
    // Rebuild the two band columns only when the set of streaming flexgrids
    // changes (canvas thrash/flicker otherwise); every tick just redraws pixels
    // into the reused canvases. Same stable role order the dashboard uses
    // (left, then right, then others) so the hands never swap sides on stream.

    let bandsSig = '';
    const bandCanvases = {};   // device_id -> {canvas, labelEl}

    function roleOrder(r) { return r === 'left' ? 0 : r === 'right' ? 1 : 2; }

    function drawHeatmaps(devices) {
        const flex = devices
            .filter(d => d.device_type === 'flexgrid'
                         && Array.isArray(d.matrix) && d.matrix.length)
            .sort((a, b) => roleOrder(a.role) - roleOrder(b.role)
                            || String(a.device_id).localeCompare(String(b.device_id)));

        if (!flex.length) {
            if (bandsSig !== '') {
                bandsEl.innerHTML = '<div class="heat-empty">Waiting for a flexgrid…</div>';
                bandsSig = '';
            }
            return;
        }

        // Shared vmax across both bands (sticky high-water-mark + 1.2x headroom,
        // never shrinks) so the two hands compare on one scale, like app.js.
        let observedMax = 0;
        for (const d of flex) for (const col of d.matrix) for (const v of col) {
            if (v > observedMax) observedMax = v;
        }
        if (observedMax > heatmapVmax) heatmapVmax = Math.min(4096, Math.floor(observedMax * 1.2));

        const sig = flex.map(d => d.device_id).join(',');
        if (sig !== bandsSig) {
            bandsEl.innerHTML = flex.map(d => {
                const role = d.role || '';
                const cls = role === 'left' ? 'role-left'
                    : role === 'right' ? 'role-right' : 'role-none';
                const tag = role ? role.charAt(0).toUpperCase() : '·';
                return `<div class="band" data-id="${escapeHtml(d.device_id)}">
                        <div class="band-label">
                            <span class="role-pill ${cls}">${escapeHtml(tag)}</span>
                            <span class="band-hz"></span>
                        </div>
                        <canvas></canvas>
                    </div>`;
            }).join('');
            bandsSig = sig;
            for (const key of Object.keys(bandCanvases)) delete bandCanvases[key];
            bandsEl.querySelectorAll('.band').forEach(el => {
                bandCanvases[el.dataset.id] = {
                    canvas: el.querySelector('canvas'),
                    labelEl: el.querySelector('.band-hz'),
                };
            });
        }

        flex.forEach(d => {
            const ref = bandCanvases[d.device_id];
            if (ref) drawBand(d, ref.canvas, ref.labelEl);
        });
    }

    function drawBand(dev, canvas, labelEl) {
        if (!canvas) return;
        const cx = canvas.getContext('2d');
        const matrix = dev.matrix;   // [cols][rows]
        if (!matrix || !matrix.length) return;
        const cols = matrix.length;
        const rows = matrix[0].length;

        if (labelEl) {
            const role = dev.role ? dev.role.toUpperCase() : '';
            labelEl.textContent = `${role ? role + ' · ' : ''}${(dev.hz || 0).toFixed(1)} Hz`;
        }

        // Size the canvas to its rendered box (the flex column drives width;
        // keep the matrix aspect). Only touch .width/.height on change.
        const w = canvas.clientWidth || 200;
        const h = canvas.clientHeight || Math.floor(w * (rows / cols) * 1.3);
        if (canvas.width !== w || canvas.height !== h) {
            canvas.width = w;
            canvas.height = h;
        }

        const cellW = w / cols;
        const cellH = h / rows;
        cx.fillStyle = '#1a1f2b';
        cx.fillRect(0, 0, w, h);
        for (let c = 0; c < cols; c++) {
            for (let r = 0; r < rows; r++) {
                cx.fillStyle = pressureColor(matrix[c][r], heatmapVmax);
                cx.fillRect(c * cellW, r * cellH, cellW - 1, cellH - 1);
            }
        }
    }

    // ---------- frames-captured counter ----------
    //
    // Hero number = paired rows captured this take (recording.rows == the
    // CSV rows training reads). We also surface sensor frames seen + match rate.
    // The REC dot color uses the dashboard's exact verdict thresholds
    // (app.js renderRecording) so GOOD/DEGRADED/BAD read the same on stream.

    function renderCounter(rec, devices) {
        if (!rec) {
            counterEl.classList.add('idle');
            recDotEl.className = 'rec-dot';
            counterTitleEl.textContent = 'IDLE - not recording';
            counterNumEl.textContent = '0';
            counterSubEl.textContent = 'press Record on the dashboard to start a take';
            counterBandsEl.textContent = '';
            return;
        }
        counterEl.classList.remove('idle');

        const rows = rec.rows ?? 0;
        const seen = rec.sensor_frames_seen ?? 0;
        const matched = rec.matched ?? 0;
        const rate = rec.match_rate ?? 0;
        const widthMiss = rec.label_width_mismatch ?? 0;
        const bandFlat = rec.band_flat === true;

        // Verdict (identical thresholds to app.js renderRecording): band_flat or
        // very low match rate or nothing matched after warmup -> BAD; a soft
        // match rate or any width mismatch -> DEGRADED; else GOOD.
        let verdict = 'good';
        if (bandFlat || rate < 0.5 || (seen > 20 && matched === 0)) {
            verdict = 'bad';
        } else if (rate < 0.9 || widthMiss > 0) {
            verdict = 'warn';
        }
        recDotEl.className = 'rec-dot rec-' + verdict;
        const verdictWord = verdict === 'good' ? 'GOOD'
            : verdict === 'warn' ? 'DEGRADED' : 'BAD';
        counterTitleEl.textContent = `REC · ${verdictWord}`;

        counterNumEl.textContent = rows.toLocaleString();

        // Elapsed: duration_s from the snapshot, shown mm:ss for a streamer glance.
        const secs = Math.max(0, Math.floor(rec.duration_s || 0));
        const mm = String(Math.floor(secs / 60)).padStart(2, '0');
        const ss = String(secs % 60).padStart(2, '0');
        counterSubEl.innerHTML =
            `paired rows · <b>${seen.toLocaleString()}</b> sensor frames · `
            + `<b>${(rate * 100).toFixed(0)}%</b> matched · ${mm}:${ss}`;

        // Per-band L/R line: the recording block doesn't carry per-band row
        // counts, but rec.sensors maps each recorded band's device_id -> role;
        // pair that with the live per-device Hz from the snapshot devices so the
        // streamer sees both bands are alive. Ordered left, right, then others.
        const sensors = rec.sensors || {};
        const hzById = {};
        for (const d of devices) hzById[d.device_id] = d.hz || 0;
        const entries = Object.entries(sensors)
            .sort((a, b) => roleOrder(a[1]) - roleOrder(b[1]));
        counterBandsEl.innerHTML = entries.map(([id, role]) => {
            const tag = role ? role.charAt(0).toUpperCase() + role.slice(1) : id;
            return `<span>${escapeHtml(tag)}: <b>${(hzById[id] || 0).toFixed(0)} Hz</b></span>`;
        }).join('');
    }

    // ---------- virtual controller (from snapshot only) ----------
    //
    // The snapshot gamepad device carries flat `values` = axes + buttons, plus
    // axis_count / button_count / mapping. We split values back into axes/buttons
    // using axis_count, then draw. Standard mapping (Xbox pads) gets the compact
    // xbox face; anything else gets bars. Panel is rebuilt only on shape change;
    // each tick only mutates transforms/opacity on cached refs.

    let padShape = '';       // '' | 'std:<a>x<b>' | 'raw:<a>x<b>' | 'none'
    let padMode = '';        // 'xbox' | 'bars'
    let xboxRefs = null;
    let barAxisEls = [];
    let barBtnEls = [];

    function buildXbox(nAxes, nButtons) {
        // Geometry mirrors gamepad.js's 400x210 face so the /stream pad reads
        // the same as the dashboard's, just standalone and snapshot-driven.
        padBodyEl.innerHTML =
            `<svg class="gp-xbox" viewBox="0 0 400 210" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">` +
            `<rect class="gp-x-trig-track" x="62" y="5" width="16" height="35" rx="3"/>` +
            `<rect class="gp-x-trig-fill" data-gpx="t6" x="64" y="39" width="12" height="0" rx="2"/>` +
            `<text class="gp-x-small-label" x="84" y="26">LT</text>` +
            `<rect class="gp-x-trig-track" x="322" y="5" width="16" height="35" rx="3"/>` +
            `<rect class="gp-x-trig-fill" data-gpx="t7" x="324" y="39" width="12" height="0" rx="2"/>` +
            `<text class="gp-x-small-label" x="316" y="26" text-anchor="end">RT</text>` +
            `<rect class="gp-x-pill" data-gpx="b4" x="40" y="46" width="78" height="14" rx="7"/>` +
            `<text class="gp-x-small-label" x="79" y="56" text-anchor="middle">LB</text>` +
            `<rect class="gp-x-pill" data-gpx="b5" x="282" y="46" width="78" height="14" rx="7"/>` +
            `<text class="gp-x-small-label" x="321" y="56" text-anchor="middle">RB</text>` +
            `<rect class="gp-x-body" x="20" y="66" width="360" height="136" rx="44"/>` +
            `<circle class="gp-x-pill" data-gpx="b16" cx="200" cy="92" r="11"/>` +
            `<rect class="gp-x-pill" data-gpx="b8" x="158" y="117" width="22" height="10" rx="5"/>` +
            `<rect class="gp-x-pill" data-gpx="b9" x="220" y="117" width="22" height="10" rx="5"/>` +
            `<circle class="gp-x-well" cx="88" cy="118" r="27"/>` +
            `<g data-gpx="s0"><circle class="gp-x-cap" data-gpx="sc10" cx="88" cy="118" r="15"/></g>` +
            `<circle class="gp-x-well" cx="248" cy="166" r="24"/>` +
            `<g data-gpx="s1"><circle class="gp-x-cap" data-gpx="sc11" cx="248" cy="166" r="13"/></g>` +
            `<circle class="gp-x-face" data-gpx="b3" cx="312" cy="95" r="11.5" style="fill:var(--gp-y);stroke:var(--gp-y)"/>` +
            `<text class="gp-x-face-label" x="312" y="99">Y</text>` +
            `<circle class="gp-x-face" data-gpx="b2" cx="289" cy="118" r="11.5" style="fill:var(--gp-x);stroke:var(--gp-x)"/>` +
            `<text class="gp-x-face-label" x="289" y="122">X</text>` +
            `<circle class="gp-x-face" data-gpx="b1" cx="335" cy="118" r="11.5" style="fill:var(--gp-b);stroke:var(--gp-b)"/>` +
            `<text class="gp-x-face-label" x="335" y="122">B</text>` +
            `<circle class="gp-x-face" data-gpx="b0" cx="312" cy="141" r="11.5" style="fill:var(--gp-a);stroke:var(--gp-a)"/>` +
            `<text class="gp-x-face-label" x="312" y="145">A</text>` +
            `<rect class="gp-x-arm-center" x="143" y="157" width="18" height="18"/>` +
            `<rect class="gp-x-arm" data-gpx="b12" x="143" y="138" width="18" height="19" rx="2"/>` +
            `<rect class="gp-x-arm" data-gpx="b13" x="143" y="175" width="18" height="19" rx="2"/>` +
            `<rect class="gp-x-arm" data-gpx="b14" x="124" y="157" width="19" height="18" rx="2"/>` +
            `<rect class="gp-x-arm" data-gpx="b15" x="161" y="157" width="19" height="18" rx="2"/>` +
            `</svg>`;

        const q = sel => padBodyEl.querySelector(sel);
        const lit = [];
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

    function buildBars(nAxes, nButtons) {
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
        padBodyEl.innerHTML =
            `<div class="gp-bars"><div class="gp-axes">${axRows.join('')}</div>` +
            `<div class="gp-btns">${btnCells}</div></div>`;
        barAxisEls = Array.from(padBodyEl.querySelectorAll('.gp-axis-fill'));
        barBtnEls = Array.from(padBodyEl.querySelectorAll('.gp-btn'));
    }

    function paintXbox(axes, buttons) {
        const r = xboxRefs;
        if (!r) return;
        for (let i = 0; i < 2; i++) {
            const dx = clamp1(axes[i * 2] || 0) * STICK_TRAVEL;
            const dy = clamp1(axes[i * 2 + 1] || 0) * STICK_TRAVEL;
            r.caps[i].setAttribute('transform', 'translate(' + dx.toFixed(1) + ' ' + dy.toFixed(1) + ')');
            r.capCircles[i].classList.toggle('on', (buttons[10 + i] || 0) > PRESS_THRESH);
        }
        for (let i = 0; i < 2; i++) {
            const h = (buttons[6 + i] || 0) * TRIG_H;
            const el = r.trigFills[i];
            el.setAttribute('height', h.toFixed(1));
            el.setAttribute('y', (TRIG_BOTTOM - h).toFixed(1));
        }
        for (let i = 0; i < r.lit.length; i++) {
            const item = r.lit[i];
            const v = buttons[item.idx] || 0;
            item.el.setAttribute('fill-opacity', (LIT_MIN + (1 - LIT_MIN) * v).toFixed(2));
            item.el.classList.toggle('on', v > PRESS_THRESH);
        }
    }

    function paintBars(axes, buttons) {
        for (let i = 0; i < barAxisEls.length; i++) {
            const v = clamp1(axes[i] || 0);
            const el = barAxisEls[i];
            const half = Math.abs(v) * 50;
            el.style.left = v >= 0 ? '50%' : (50 - half) + '%';
            el.style.width = half + '%';
            el.classList.toggle('neg', v < 0);
        }
        for (let i = 0; i < barBtnEls.length; i++) {
            const v = buttons[i] || 0;
            const el = barBtnEls[i];
            el.style.opacity = (0.25 + 0.75 * v).toFixed(2);
            el.classList.toggle('on', v > 0.5);
        }
    }

    function renderController(devices) {
        // Freshness guard mirrors the dashboard: a stale pad (no recent frame)
        // shouldn't keep drawing a frozen controller. 2.5s tolerates the pad's
        // ~50Hz send comfortably.
        const pad = devices.find(d => d.device_type === 'gamepad'
                                      && (d.last_seen_age == null || d.last_seen_age < 2.5));
        if (!pad) {
            if (padShape !== 'none') {
                padBodyEl.innerHTML = '<div class="pad-empty">no controller</div>';
                padNameEl.textContent = '';
                padShape = 'none';
                xboxRefs = null; barAxisEls = []; barBtnEls = [];
            }
            return;
        }

        // Reconstruct axes vs buttons from the flat snapshot values using the
        // relayed axis_count (values = axes + buttons; see ingest_gamepad_packet).
        const values = Array.isArray(pad.values) ? pad.values : [];
        const nAxes = pad.axis_count != null ? pad.axis_count : 0;
        const nButtons = pad.button_count != null ? pad.button_count
            : Math.max(0, values.length - nAxes);
        const axes = values.slice(0, nAxes);
        const buttons = values.slice(nAxes, nAxes + nButtons);
        const mapping = pad.mapping || '';

        // Same standard-shape gate gamepad.js uses: standard mapping + >=4 axes
        // and >=16 buttons -> xbox face; otherwise the generic bars fallback.
        const standard = mapping === 'standard' && nAxes >= 4 && nButtons >= 16;
        const shape = `${standard ? 'std' : 'raw'}:${nAxes}x${nButtons}`;
        if (shape !== padShape) {
            padShape = shape;
            xboxRefs = null; barAxisEls = []; barBtnEls = [];
            padMode = standard ? 'xbox' : 'bars';
            if (standard) buildXbox(nAxes, nButtons);
            else buildBars(nAxes, nButtons);
        }
        padNameEl.textContent = pad.device_id || 'controller';

        if (padMode === 'xbox') paintXbox(axes, buttons);
        else paintBars(axes, buttons);
    }

    // ---------- /ws/live socket ----------

    let ws = null;

    function wsURL() {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        return `${proto}://${location.host}/ws/live`;
    }

    function onSnapshot(msg) {
        const devices = msg.devices || [];
        drawHeatmaps(devices);
        renderCounter(msg.recording || null, devices);
        renderController(devices);
    }

    function connect() {
        try {
            ws = new WebSocket(wsURL());
        } catch (e) {
            setTimeout(connect, RECONNECT_MS);
            return;
        }
        ws.onopen = () => { connEl.classList.remove('show'); };
        ws.onmessage = ev => {
            let msg;
            try { msg = JSON.parse(ev.data); } catch (e) { return; }
            if (msg && msg.type === 'tick') onSnapshot(msg);
        };
        ws.onclose = () => { ws = null; connEl.classList.add('show'); setTimeout(connect, RECONNECT_MS); };
        ws.onerror = () => { try { ws.close(); } catch (e) {} };
    }

    connect();
})();

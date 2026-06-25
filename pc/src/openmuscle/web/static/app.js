// OpenMuscle Web UI — live heatmap, recording, capture management.
// Single-page vanilla JS. No bundler, no framework.

const wsStatus    = document.getElementById('ws-status');
const deviceList  = document.getElementById('device-list');
const canvas      = document.getElementById('heatmap');
const ctx         = canvas.getContext('2d');
const heatmapMeta = document.getElementById('heatmap-meta');
const recordBtn   = document.getElementById('record-btn');
const recordMultibandBtn = document.getElementById('record-multiband-btn');
const recordStatus= document.getElementById('record-status');
const captureName = document.getElementById('capture-name');
const capturesBody= document.getElementById('captures-body');
const sensorSelect= document.getElementById('sensor-select');
const labelSelect = document.getElementById('label-select');
const trainBtn    = document.getElementById('train-btn');
const trainStatus = document.getElementById('train-status');
const selStatus   = document.getElementById('captures-sel-status');
const checkAll    = document.getElementById('captures-check-all');
const modelsBody  = document.getElementById('models-body');
const modelsCount = document.getElementById('models-count');
const openFolderBtn = document.getElementById('captures-open-folder');

// Ask the server to open the captures folder in the OS file manager.
// If `name` is given, highlight that capture file inside the folder.
async function revealCaptureFolder(name) {
    try {
        const r = await fetch('/api/reveal', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name || null}),
        });
        if (!r.ok) throw new Error(await readError(r));
    } catch (e) {
        alert('Could not open folder: ' + e.message);
    }
}

if (openFolderBtn) {
    openFolderBtn.onclick = () => revealCaptureFolder(null);
}

// Per-user pick preferences that survive a refresh
const STORE_SENSOR = 'om.sensor_device_id';
const STORE_LABEL  = 'om.label_device_id';
const STORE_HAND   = 'om.hand_target';      // last successfully-applied "host:port" — auto-restored on next launch

// Set of capture filenames currently checked in the table
const selectedCaptures = new Set();

let selectedDeviceId = null;
let lastDevices = [];
let recordingState = null;        // null when idle; {filename, rows, duration_s} when recording
let activeSession = null;          // null when no session active; {id, name, arm, ...} otherwise
let inferenceState = null;         // last inference snapshot, used for REC+LIVE detection

// ---------- WebSocket ----------

function connectWS() {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/live`);

    ws.onopen = () => {
        wsStatus.textContent = 'connected';
        wsStatus.className = 'badge online';
        // Re-arm the hand-target auto-restore: every fresh WS connect (which
        // includes server restarts) gets a chance to re-apply the saved hand
        // target. Otherwise the operator has to remember to click Apply
        // after every `openmuscle web` restart.
        handTargetRestoreAttempted = false;
    };
    ws.onclose = () => {
        wsStatus.textContent = 'disconnected';
        wsStatus.className = 'badge offline';
        setTimeout(connectWS, 1000);
    };
    ws.onerror = () => { /* close handler will retry */ };
    ws.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            handleTick(msg);
        } catch (err) {
            console.warn('bad ws payload', err);
        }
    };
}

function handleTick(msg) {
    if (msg.type !== 'tick') return;
    lastDevices = msg.devices || [];
    recordingState = msg.recording || null;
    inferenceState = msg.inference || null;
    const prevSessionId = activeSession ? activeSession.id : null;
    activeSession = msg.active_session || null;
    if (prevSessionId !== (activeSession ? activeSession.id : null)) {
        // Session changed -> re-fetch captures (server-side meta seeding
        // means the row list may show new session_id tags).
        refreshCaptures();
    }
    renderActiveSession();
    renderDevices();
    renderDiscovery(msg.discovery || []);
    renderRecordPickers();
    renderRecording();
    const dev = selectedDevice();
    if (dev) {
        drawHeatmap(dev);
    }
    // LASK5: render whichever LASK device is currently streaming.
    // (We don't require it to be the "selected" device — operators usually
    // want to see the FlexGrid heatmap and the LASK pistons at the same time.)
    const lask = lastDevices.find(d => d.device_type === 'lask5');
    renderLask(lask);
    renderInference(msg.inference);
    // Comparator + top-bar pipeline strip are Studio-shell additions.
    // They derive everything from the per-tick snapshot, so they update
    // in lockstep with the underlying bars and the WS message.
    renderResiduals(lask, msg.inference);
    renderPipelinePills(msg, lask);
    // quest_hand 3D viewer: when a hand label source is streaming, swap the
    // LASK5 piston comparator for a live 3D hand (the pistons are zeros for
    // a hand source). No-op when no quest_hand device is present.
    renderHandViewer(lastDevices.find(d => d.device_type === 'quest_hand'),
                     msg.inference);
    // IMU orientation widget: drive from a device carrying the fast data.imu
    // (prefer the selected device; else the first with imu).
    renderImuViewer();
}

// ---------- IMU orientation widget ----------

function renderImuViewer() {
    const wrap = document.getElementById('imu-viewer');
    if (!wrap || !window.OMImuViewer) return;
    const sel = selectedDevice();
    const dev = (sel && sel.imu && Array.isArray(sel.imu.accel)) ? sel
        : lastDevices.find(d => d.imu && Array.isArray(d.imu.accel));
    if (!dev) {
        wrap.style.display = 'none';
        if (window.OMImuViewer.isReady()) window.OMImuViewer.setVisible(false);
        return;
    }
    if (!window.OMImuViewer.isReady()) {
        const el = document.getElementById('imu-viewer-canvas');
        if (el) window.OMImuViewer.init(el);
    }
    if (!window.OMImuViewer.isReady()) return;
    wrap.style.display = 'flex';
    window.OMImuViewer.setVisible(true);
    window.OMImuViewer.update(dev.imu);
    const axes = document.getElementById('imu-axes');
    if (axes) axes.textContent = escapeHtml(dev.device_id);
}

// ---------- quest_hand 3D viewer ----------

// Drives the Three.js hand viewer (window.OMHandViewer, loaded as a module).
// Shows the REAL captured hand from the live quest_hand device's flat joint
// `values`, plus the model's PREDICTED hand from inference.piston_values when
// a quest-trained model (>= 25 joints * 7 floats) is running. Toggles the
// .hand-mode class on .comparator so CSS hides the LASK5 pistons in favor of
// the viewer.
function renderHandViewer(questDev, inference) {
    const comparator = document.querySelector('.comparator');
    const viewerReady = window.OMHandViewer && window.OMHandViewer.isReady;
    if (!questDev) {
        if (comparator) comparator.classList.remove('hand-mode');
        if (viewerReady && window.OMHandViewer.isReady()) window.OMHandViewer.setVisible(false);
        return;
    }
    // Lazy-init the viewer on first quest_hand sighting (the module may still
    // be loading right at page open; guard with isReady).
    if (window.OMHandViewer && !window.OMHandViewer.isReady()) {
        const el = document.getElementById('hand-viewer-canvas');
        if (el) window.OMHandViewer.init(el);
    }
    if (!(window.OMHandViewer && window.OMHandViewer.isReady())) return;

    if (comparator) comparator.classList.add('hand-mode');
    window.OMHandViewer.setVisible(true);

    const realFlat = Array.isArray(questDev.values) ? questDev.values : null;
    // Predicted hand: only when the live model emits a full hand vector.
    let predFlat = null;
    const pv = inference && inference.piston_values;
    if (Array.isArray(pv) && pv.length >= 25 * 7) predFlat = pv;
    window.OMHandViewer.update(realFlat, predFlat);

    // Reuse the existing GT meta slot to label the hand source.
    const gtMeta = document.getElementById('lask-meta');
    if (gtMeta) {
        const hz = (typeof questDev.hz === 'number') ? questDev.hz.toFixed(0) : '0';
        const nJoints = realFlat ? Math.floor(realFlat.length / 7) : 0;
        gtMeta.textContent = `Quest hand · ${nJoints} joints · ${hz} Hz`;
    }
}

// ---------- native V4 discovery (Sources rail) ----------

let _discoveryProbeWired = false;

function renderDiscovery(discovery) {
    const list = document.getElementById('discovery-list');
    const count = document.getElementById('discovery-count');
    if (!list) return;
    if (!_discoveryProbeWired) wireDiscoveryProbe();

    const subs = discovery.filter(d => d.subscribed).length;
    if (count) count.textContent = discovery.length
        ? `${subs}/${discovery.length} subscribed` : '';

    if (!discovery.length) {
        list.innerHTML = '<li class="empty">No V4 sources discovered yet…</li>';
        return;
    }
    list.innerHTML = discovery.map(d => {
        // State badge: subscribed (green) / error (red) / known (grey).
        let stateCls = 'known', stateTxt = 'known';
        if (d.subscribed) { stateCls = 'subscribed'; stateTxt = 'subscribed'; }
        else if (d.sub_error) { stateCls = 'err'; stateTxt = 'error'; }
        const btnTxt = d.subscribed ? 'Unsubscribe' : 'Subscribe';
        const btnAct = d.subscribed ? 'unsubscribe' : 'subscribe';
        const age = (d.age_s != null) ? `${d.age_s.toFixed(0)}s ago` : '';
        const errLine = d.sub_error
            ? `<div class="src-err" title="${escapeHtml(d.sub_error)}">${escapeHtml(d.sub_error)}</div>`
            : '';
        return `
            <li class="src ${stateCls}" data-id="${escapeHtml(d.device_id)}">
                <div class="src-top">
                    <span class="src-id">${escapeHtml(d.device_id)}</span>
                    <span class="src-state ${stateCls}">${stateTxt}</span>
                </div>
                <div class="src-meta">
                    <span class="type">${escapeHtml(d.device_type)}</span>
                    <span class="addr">${escapeHtml(d.ip)}:${d.cmd_port}</span>
                    <span class="via">via ${escapeHtml(d.source)}</span>
                    <span class="age">${age}</span>
                </div>
                ${errLine}
                <label class="src-role">role
                    <select class="src-role-sel" data-id="${escapeHtml(d.device_id)}">
                        <option value=""${d.role ? '' : ' selected'}>untagged</option>
                        <option value="left"${d.role === 'left' ? ' selected' : ''}>left</option>
                        <option value="right"${d.role === 'right' ? ' selected' : ''}>right</option>
                        <option value="labeler"${d.role === 'labeler' ? ' selected' : ''}>labeler</option>
                    </select>
                </label>
                <button class="src-btn" data-act="${btnAct}" data-id="${escapeHtml(d.device_id)}">${btnTxt}</button>
            </li>`;
    }).join('');

    list.querySelectorAll('.src-role-sel').forEach(sel => {
        sel.onchange = async () => {
            const id = sel.dataset.id;
            const role = sel.value;
            try {
                const res = await fetch('/api/discovery/role', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ device_id: id, role }),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    setProbeMsg(err.detail || 'set role failed', true);
                } else {
                    setProbeMsg(`${id} → ${role || 'untagged'}`, false);
                }
            } catch (err) {
                setProbeMsg(String(err), true);
            }
        };
    });

    list.querySelectorAll('.src-btn').forEach(btn => {
        btn.onclick = async (e) => {
            e.stopPropagation();
            const id = btn.dataset.id;
            const act = btn.dataset.act;
            btn.disabled = true;
            btn.textContent = act === 'subscribe' ? 'Subscribing…' : 'Unsubscribing…';
            try {
                const res = await fetch(`/api/discovery/${act}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ device_id: id }),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    setProbeMsg(err.detail || `${act} failed`, true);
                }
            } catch (err) {
                setProbeMsg(String(err), true);
            }
            // Next WS tick re-renders the true state; no manual refresh needed.
        };
    });
}

function wireDiscoveryProbe() {
    const form = document.getElementById('discovery-probe-form');
    const input = document.getElementById('discovery-probe-ip');
    if (!form || !input) return;
    _discoveryProbeWired = true;
    form.onsubmit = async (e) => {
        e.preventDefault();
        const raw = input.value.trim();
        if (!raw) return;
        // Accept "ip" or "ip:port".
        let ip = raw, cmd_port = null;
        if (raw.includes(':')) {
            const parts = raw.split(':');
            ip = parts[0];
            const p = parseInt(parts[1], 10);
            if (!isNaN(p)) cmd_port = p;
        }
        setProbeMsg(`probing ${ip}…`, false);
        try {
            const res = await fetch('/api/discovery/probe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(cmd_port ? { ip, cmd_port } : { ip }),
            });
            if (res.ok) {
                const d = await res.json();
                setProbeMsg(`found ${d.device_id} (${d.device_type})`, false);
                input.value = '';
            } else {
                const err = await res.json().catch(() => ({}));
                setProbeMsg(err.detail || `no V4 source at ${ip}`, true);
            }
        } catch (err) {
            setProbeMsg(String(err), true);
        }
    };
}

function setProbeMsg(text, isError) {
    const el = document.getElementById('discovery-probe-msg');
    if (!el) return;
    el.textContent = text;
    el.classList.toggle('err', !!isError);
}

// ---------- device list ----------

function selectedDevice() {
    if (!lastDevices.length) return null;
    if (selectedDeviceId) {
        const d = lastDevices.find(d => d.device_id === selectedDeviceId);
        if (d) return d;
    }
    // Auto-select the most recently active device
    selectedDeviceId = lastDevices[0].device_id;
    return lastDevices[0];
}

function renderDevices() {
    if (!lastDevices.length) {
        deviceList.innerHTML = '<li class="empty">Waiting for a device to send a packet…</li>';
        return;
    }
    const html = lastDevices.map(d => {
        const isSel = (d.device_id === selectedDeviceId);
        const stale = d.last_seen_age > 2.0;
        const statusLine = renderDeviceStatus(d);
        return `
            <li class="device ${isSel ? 'selected' : ''} ${stale ? 'stale' : ''}"
                data-id="${d.device_id}">
                <div class="device-id">${escapeHtml(d.device_id)}</div>
                <div class="device-meta">
                    <span class="type">${escapeHtml(d.device_type)}</span>
                    <span class="shape">${d.rows}×${d.cols}</span>
                    <span class="hz">${d.hz.toFixed(1)} Hz</span>
                    <span class="age">${stale ? `${d.last_seen_age.toFixed(1)}s` : 'live'}</span>
                </div>
                ${statusLine}
            </li>`;
    }).join('');
    deviceList.innerHTML = html;
    deviceList.querySelectorAll('li.device').forEach(el => {
        el.onclick = () => {
            selectedDeviceId = el.dataset.id;
            renderDevices();
        };
    });
}

// Battery + uptime + rssi line under the device meta row. Returns '' when
// the device never reported a meta field (legacy firmware).
function renderDeviceStatus(d) {
    // status (slow ~1Hz meta) may be absent while imu (fast data.imu) is present,
    // so default s to {} and let each status part guard itself.
    const s = d.status || {};

    const parts = [];

    // Battery: prefer pct + voltage when both are present, color-coded
    if (typeof s.vbat === 'number' || typeof s.pct === 'number') {
        const v = (typeof s.vbat === 'number') ? s.vbat.toFixed(2) + 'V' : null;
        const pct = (typeof s.pct === 'number') ? s.pct + '%' : null;
        let cls = 'bat-good';
        if (typeof s.pct === 'number') {
            if      (s.pct < 15) cls = 'bat-crit';
            else if (s.pct < 40) cls = 'bat-warn';
        } else if (typeof s.vbat === 'number') {
            if      (s.vbat < 3.55) cls = 'bat-crit';
            else if (s.vbat < 3.75) cls = 'bat-warn';
        }
        const batText = [v, pct].filter(Boolean).join(' ');
        parts.push(`<span class="bat ${cls}">🔋 ${escapeHtml(batText)}</span>`);
    }

    // Uptime in compact form: 1234s -> 20m 34s -> 3h 22m
    if (typeof s.uptime_s === 'number') {
        parts.push(`<span class="up">⏱ ${escapeHtml(formatUptime(s.uptime_s))}</span>`);
    }

    // RSSI (only if we have it). ESP32 reports negative dBm.
    if (typeof s.rssi === 'number') {
        let cls = 'rssi-ok';
        if      (s.rssi < -80) cls = 'rssi-bad';
        else if (s.rssi < -67) cls = 'rssi-warn';
        parts.push(`<span class="rssi ${cls}">📶 ${s.rssi} dBm</span>`);
    }

    // Reboot indicator: only shown when the device has reset at least
    // once this PC session. Includes how long ago + the reason (e.g.
    // WDT = task hung, POWER_ON = cold boot or brownout).
    if (d.reboot_count && d.reboot_count > 0) {
        const age = (typeof d.last_reboot_age === 'number')
            ? formatUptime(d.last_reboot_age) + ' ago'
            : '?';
        const why = d.last_reset_cause ? ` (${escapeHtml(String(d.last_reset_cause))})` : '';
        parts.push(`<span class="reboots">⟳ ${d.reboot_count} reboot${d.reboot_count === 1 ? '' : 's'}, last ${age}${why}</span>`);
    }

    // IMU readout (fast data.imu path, ~18-20Hz): per-axis gyro + accel raw
    // counts. Matches phone's readout; the 3D orientation widget builds on this.
    if (d.imu && Array.isArray(d.imu.gyro) && Array.isArray(d.imu.accel)) {
        const g = d.imu.gyro, a = d.imu.accel;
        parts.push(`<span class="imu" title="data.imu (raw counts): gyro then accel">`
            + `🧭 g ${g[0]},${g[1]},${g[2]} · a ${a[0]},${a[1]},${a[2]}</span>`);
    }

    if (!parts.length) return '';
    return `<div class="device-status">${parts.join(' ')}</div>`;
}

function formatUptime(s) {
    s = Math.floor(s);
    if (s < 60)   return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return h + 'h ' + m + 'm';
}

// ---------- heatmap ----------

// Heatmap color/range tunables — user can adjust to taste later.
const HEATMAP_NOISE_GATE = 8;       // below this, treat as "untouched"
const HEATMAP_VMAX_DEFAULT = 2000;  // ADC value that maps to peak color
let heatmapVmax = HEATMAP_VMAX_DEFAULT;

function drawHeatmap(dev) {
    const matrix = dev.matrix;  // [cols][rows]
    if (!matrix || !matrix.length) return;
    const cols = matrix.length;
    const rows = matrix[0].length;

    // Auto-adjust vmax upward if we see a value much higher than current scale;
    // never auto-shrink (sticky high-water-mark plus 1.2x headroom).
    let observedMax = 0;
    for (let c = 0; c < cols; c++) {
        for (let r = 0; r < rows; r++) {
            if (matrix[c][r] > observedMax) observedMax = matrix[c][r];
        }
    }
    if (observedMax * 1.0 > heatmapVmax) {
        heatmapVmax = Math.min(4096, Math.floor(observedMax * 1.2));
    }

    heatmapMeta.textContent =
        `${dev.device_id} · ${rows}×${cols} · ${dev.hz.toFixed(1)} Hz · ${dev.packets} pkts · max=${observedMax} · vmax=${heatmapVmax}`;

    // Resize canvas to fit the matrix aspect ratio nicely
    const w = canvas.clientWidth;
    const h = Math.max(160, Math.floor(w * (rows / cols) * 1.3));
    if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
    }

    const cellW = w / cols;
    const cellH = h / rows;

    // Solid background — cells fully overdraw it.
    ctx.fillStyle = '#1a1f2b';
    ctx.fillRect(0, 0, w, h);

    for (let c = 0; c < cols; c++) {
        for (let r = 0; r < rows; r++) {
            const v = matrix[c][r];
            ctx.fillStyle = pressureColor(v, heatmapVmax);
            ctx.fillRect(c * cellW, r * cellH, cellW - 1, cellH - 1);
            // Show numeric value once it's above the noise gate — useful for
            // seeing exactly how much "bleed" a neighbor cell has.
            if (v >= 50) {
                const t = v / heatmapVmax;
                ctx.fillStyle = t > 0.55 ? '#0b0d12' : '#e7e9ee';
                ctx.font = `${Math.floor(Math.min(cellW, cellH) * 0.30)}px ui-monospace, monospace`;
                ctx.textBaseline = 'middle';
                ctx.textAlign = 'center';
                ctx.fillText(v, c * cellW + cellW / 2, r * cellH + cellH / 2);
            }
        }
    }
}

// "Inferno"-style ramp with a clearly visible low end. Anything above the
// noise gate gets a perceptible color; only the truly idle cells stay near
// the background.
function pressureColor(v, vmax) {
    if (v < HEATMAP_NOISE_GATE) return '#1a1f2b';
    const t = Math.max(0, Math.min(1, v / vmax));
    const stops = [
        [40,  45,  90 ],   // soft blue (just-above-noise)
        [85,  40,  140],   // purple
        [165, 45,  140],   // magenta
        [225, 90,  90 ],   // pink/red
        [255, 165, 60 ],   // orange
        [255, 230, 90 ],   // yellow
    ];
    const seg = Math.min(stops.length - 2, Math.floor(t * (stops.length - 1)));
    const localT = (t * (stops.length - 1)) - seg;
    const a = stops[seg], b = stops[seg + 1];
    const lerp = (x, y) => Math.round(x + (y - x) * localT);
    return `rgb(${lerp(a[0],b[0])},${lerp(a[1],b[1])},${lerp(a[2],b[2])})`;
}

// ---------- recording ----------

// ---------- record device pickers ----------

function renderRecordPickers() {
    // Rebuild dropdown options to match the current device list, preserving
    // any user-chosen selection that's still present. We avoid rebuilding on
    // every tick if the options would be unchanged -- otherwise an open
    // <select> closes on every WS message.
    fillDeviceSelect(sensorSelect, lastDevices.filter(d => d.device_type === 'flexgrid'),
                     localStorage.getItem(STORE_SENSOR), '(auto-pick flexgrid)');
    fillDeviceSelect(labelSelect, lastDevices.filter(d => d.device_type === 'lask5'),
                     localStorage.getItem(STORE_LABEL), '(auto-pick lask5)',
                     /*allowNone=*/ true);

    // Disable both pickers while recording so the user can't accidentally
    // change the active stream out from under the matcher.
    const recording = !!recordingState;
    sensorSelect.disabled = recording;
    labelSelect.disabled = recording;
}

function fillDeviceSelect(sel, devices, preferredId, autoLabel, allowNone) {
    // Compute desired option list as id strings
    const desired = [''].concat(devices.map(d => d.device_id));
    if (allowNone) desired.push('__none__');

    const current = Array.from(sel.options).map(o => o.value);
    const sameKeys = current.length === desired.length
                  && current.every((v, i) => v === desired[i]);

    if (!sameKeys) {
        const prevValue = sel.value;
        sel.innerHTML = '';
        // First option = blank = "let the server auto-pick"
        const optAuto = document.createElement('option');
        optAuto.value = '';
        optAuto.textContent = autoLabel;
        sel.appendChild(optAuto);
        for (const d of devices) {
            const o = document.createElement('option');
            o.value = d.device_id;
            o.textContent = `${d.device_id} · ${d.device_type}`;
            sel.appendChild(o);
        }
        if (allowNone) {
            const o = document.createElement('option');
            o.value = '__none__';
            o.textContent = '(no label / sensor-only)';
            sel.appendChild(o);
        }
        // Restore selection
        if (preferredId && desired.includes(preferredId)) sel.value = preferredId;
        else if (prevValue && desired.includes(prevValue)) sel.value = prevValue;
    }
}

sensorSelect.addEventListener('change', () => {
    if (sensorSelect.value) localStorage.setItem(STORE_SENSOR, sensorSelect.value);
    else localStorage.removeItem(STORE_SENSOR);
});
labelSelect.addEventListener('change', () => {
    if (labelSelect.value) localStorage.setItem(STORE_LABEL, labelSelect.value);
    else localStorage.removeItem(STORE_LABEL);
});

// ---------- sessions panel ----------

const sessionStartBtn      = document.getElementById('session-start-btn');
const activeSessionArea    = document.getElementById('active-session-area');
const pastSessionsToggle   = document.getElementById('past-sessions-toggle');
const pastSessionsList     = document.getElementById('past-sessions-list');
const sessionModal         = document.getElementById('session-modal');
const sessionForm          = document.getElementById('session-form');
const capturesFilterLabel  = document.getElementById('captures-filter-label');

let pastSessions = [];

function renderActiveSession() {
    if (activeSession) {
        const s = activeSession;
        const dur = s.started_at ? Math.floor(Date.now()/1000 - s.started_at) : 0;
        const armCls = s.arm === 'left' ? 'arm-left' : (s.arm === 'right' ? 'arm-right' : '');
        const armBit = s.arm ? `<span class="${armCls}">${escapeHtml(s.arm)} arm</span>` : '<span class="empty">no arm set</span>';
        const subj = s.subject ? ' · ' + escapeHtml(s.subject) : '';
        const gestures = (s.gestures || []).length
            ? ' · planned: ' + escapeHtml((s.gestures || []).join(', '))
            : '';
        activeSessionArea.innerHTML = `
            <div class="session-card active">
                <div class="session-head">
                    <div>
                        <span class="session-id">${escapeHtml(s.name || s.id)}</span>
                        <span class="session-meta-line">${armBit}${subj} · ${s.capture_count || 0} captures · ${formatUptime(dur)}${gestures}</span>
                    </div>
                    <div class="session-actions">
                        <button class="link" id="active-session-add-btn" title="Retroactively add past captures to this session">＋ Add</button>
                        <button class="link" data-edit-session="${escapeHtml(s.id)}">edit</button>
                        <button class="link danger" id="session-end-btn">■ End session</button>
                    </div>
                </div>
                ${s.notes ? `<div class="session-meta-line" style="margin-top:6px">${escapeHtml(s.notes)}</div>` : ''}
            </div>`;
        document.getElementById('session-end-btn').onclick = endSession;
        const addBtn = document.getElementById('active-session-add-btn');
        if (addBtn) addBtn.onclick = () => openLinkModal(activeSession);
        sessionStartBtn.disabled = true;
        sessionStartBtn.title = 'End the current session before starting a new one';
        capturesFilterLabel.textContent = `· filtered to ${s.name || s.id}`;
    } else {
        activeSessionArea.innerHTML = '<div class="session-empty">No active session — recordings won\'t be grouped. Click "New session" to start one.</div>';
        sessionStartBtn.disabled = false;
        sessionStartBtn.title = '';
        capturesFilterLabel.textContent = '';
    }
}

async function refreshPastSessions() {
    try {
        const r = await fetch('/api/sessions');
        if (!r.ok) return;
        const list = await r.json();
        // Filter out the active one (already shown above)
        const activeId = activeSession ? activeSession.id : null;
        pastSessions = list.filter(s => s.id !== activeId);
        renderPastSessions();
    } catch (e) { /* best-effort */ }
}

// Sessions whose capture list is currently expanded in the UI. Persisted
// across re-renders (refreshPastSessions can fire on its own) so a poll
// doesn't collapse what the user just opened.
const expandedSessions = new Set();

// ---------- Add-captures-to-session picker modal ----------
//
// Lets the operator retroactively assign past recordings (made without an
// active session) to a session. The picker shows every capture NOT
// currently linked to the target session, with checkboxes for bulk add.
//
// Wires up:
//   - "+ Add captures" button in each past-session card
//   - "×" remove button on each capture in the expanded view

const linkModal       = document.getElementById('link-modal');
const linkSessionName = document.getElementById('link-session-name');
const linkCaptureList = document.getElementById('link-capture-list');
const linkAddBtn      = document.getElementById('link-add-btn');
let linkSessionId     = null;            // current session being edited
const linkSelected    = new Set();       // capture names currently checked

function openLinkModal(session) {
    linkSessionId = session.id;
    linkSelected.clear();
    linkSessionName.textContent = session.name || session.id;
    linkAddBtn.disabled = true;
    linkAddBtn.textContent = 'Add 0 captures';
    linkCaptureList.innerHTML = '<div class="empty">Loading captures…</div>';
    linkModal.classList.add('open');
    linkModal.setAttribute('aria-hidden', 'false');

    // Fetch the full capture list, filter out ones already in this session.
    fetch('/api/captures')
        .then(r => r.ok ? r.json() : Promise.reject('fetch failed'))
        .then(list => {
            const alreadyLinked = new Set(session.captures || []);
            const candidates = list.filter(c => !alreadyLinked.has(c.name));
            if (!candidates.length) {
                linkCaptureList.innerHTML = '<div class="empty">All captures are already in this session.</div>';
                return;
            }
            // Render rows with checkbox + name + meta summary + (if linked
            // to a different session) an annotation so the operator doesn't
            // accidentally yank a capture out of another session.
            linkCaptureList.innerHTML = candidates.map(c => {
                const meta = c.meta || {};
                const otherSession = (meta.tags || []).find(t => t.startsWith('session:'));
                const otherNote = otherSession
                    ? `<span class="link-other-session" title="Linked to ${escapeHtml(otherSession.slice(8))}">⚠ ${escapeHtml(otherSession)}</span>`
                    : '';
                const kb = (c.size_bytes / 1024).toFixed(1);
                return `<label class="link-capture-row">
                    <input type="checkbox" data-name="${escapeHtml(c.name)}">
                    <span class="link-capture-name">${escapeHtml(c.name)}</span>
                    <span class="link-capture-size">${kb} KB</span>
                    ${otherNote}
                </label>`;
            }).join('');
            linkCaptureList.querySelectorAll('input[type=checkbox]').forEach(cb => {
                cb.onchange = () => {
                    if (cb.checked) linkSelected.add(cb.dataset.name);
                    else            linkSelected.delete(cb.dataset.name);
                    const n = linkSelected.size;
                    linkAddBtn.disabled = (n === 0);
                    linkAddBtn.textContent = `Add ${n} capture${n === 1 ? '' : 's'}`;
                };
            });
        })
        .catch(err => {
            linkCaptureList.innerHTML = '<div class="empty">Could not load captures.</div>';
            console.warn('link picker fetch:', err);
        });
}

function closeLinkModal() {
    linkModal.classList.remove('open');
    linkModal.setAttribute('aria-hidden', 'true');
    linkSessionId = null;
    linkSelected.clear();
}

linkModal.querySelectorAll('[data-close]').forEach(el => {
    el.addEventListener('click', closeLinkModal);
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && linkModal.classList.contains('open')) closeLinkModal();
});

linkAddBtn.onclick = async () => {
    if (!linkSessionId || linkSelected.size === 0) return;
    linkAddBtn.disabled = true;
    linkAddBtn.textContent = 'Adding…';
    try {
        const r = await fetch(`/api/sessions/${encodeURIComponent(linkSessionId)}/captures`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({capture_names: [...linkSelected]}),
        });
        if (!r.ok) throw new Error(await readError(r));
        const result = await r.json();
        if ((result.skipped || []).length) {
            // Surface skips inline -- e.g. "already in another session"
            console.warn('some captures skipped:', result.skipped);
        }
        closeLinkModal();
        await refreshPastSessions();
        await refreshCaptures();
    } catch (e) {
        alert('Add failed: ' + (e.message || e));
        linkAddBtn.disabled = false;
        const n = linkSelected.size;
        linkAddBtn.textContent = `Add ${n} capture${n === 1 ? '' : 's'}`;
    }
};

async function removeCaptureFromSession(sessionId, captureName) {
    if (!confirm(`Remove ${captureName} from this session?\n(The capture file itself stays — just the link is cleared.)`)) return;
    try {
        const r = await fetch(
            `/api/sessions/${encodeURIComponent(sessionId)}/captures/${encodeURIComponent(captureName)}`,
            {method: 'DELETE'}
        );
        if (!r.ok) throw new Error(await readError(r));
        await refreshPastSessions();
        await refreshCaptures();
    } catch (e) {
        alert('Remove failed: ' + (e.message || e));
    }
}

function renderPastSessions() {
    if (!pastSessions.length) {
        pastSessionsList.innerHTML = '<div class="session-empty">No past sessions yet.</div>';
        return;
    }
    pastSessionsList.innerHTML = pastSessions.map(s => {
        const dur = (s.ended_at && s.started_at) ? Math.floor(s.ended_at - s.started_at) : null;
        const armBit = s.arm ? escapeHtml(s.arm) + ' arm' : '—';
        const captureList = Array.isArray(s.captures) ? s.captures : [];
        const captureCount = s.capture_count != null ? s.capture_count : captureList.length;
        const isOpen = expandedSessions.has(s.id);
        const caret = captureList.length ? (isOpen ? '▾' : '▸') : '·';
        // The captures sub-list is a sibling div, toggled by .hidden. We
        // render it eagerly (with .hidden if closed) so the open/close
        // animation isn't required and so screen readers can find it.
        const capturesInner = captureList.length
            ? captureList.map(name => `
                <li class="session-capture-row" data-name="${escapeHtml(name)}">
                    <span class="session-capture-name">${escapeHtml(name)}</span>
                    <span class="session-capture-actions">
                        <button class="link" data-reveal-cap="${escapeHtml(name)}" title="Show in file manager">📂</button>
                        <button class="link" data-edit-cap="${escapeHtml(name)}">edit</button>
                        <a href="/api/captures/${encodeURIComponent(name)}/download" download>download</a>
                        <button class="link danger" data-unlink-cap="${escapeHtml(name)}" data-from-session="${escapeHtml(s.id)}" title="Remove from this session (file stays)">×</button>
                    </span>
                </li>`).join('')
            : '<li class="session-capture-empty">No captures linked to this session.</li>';

        return `<div class="session-card" data-session="${escapeHtml(s.id)}">
            <div class="session-head session-head-clickable" data-toggle-session="${escapeHtml(s.id)}">
                <div>
                    <span class="session-caret">${caret}</span>
                    <span class="session-id">${escapeHtml(s.name || s.id)}</span>
                    <span class="session-meta-line">${armBit} · ${escapeHtml(s.subject || '—')} · ${captureCount} captures${dur != null ? ' · ' + formatUptime(dur) : ''}</span>
                </div>
                <div class="session-actions">
                    <button class="link" data-add-to-session="${escapeHtml(s.id)}" title="Retroactively add past captures to this session">＋ Add</button>
                    <button class="link" data-edit-session="${escapeHtml(s.id)}">edit</button>
                    <button class="link danger" data-delete-session="${escapeHtml(s.id)}">delete</button>
                </div>
            </div>
            ${s.notes ? `<div class="session-meta-line" style="margin-top:6px">${escapeHtml(s.notes)}</div>` : ''}
            <ul class="session-captures-list ${isOpen ? '' : 'hidden'}">${capturesInner}</ul>
        </div>`;
    }).join('');

    // Stop session-action buttons from triggering the row-toggle handler
    pastSessionsList.querySelectorAll('.session-actions button').forEach(btn => {
        btn.addEventListener('click', e => e.stopPropagation());
    });

    // Toggle expand/collapse when the session header row is clicked
    pastSessionsList.querySelectorAll('[data-toggle-session]').forEach(head => {
        head.onclick = () => {
            const sid = head.dataset.toggleSession;
            if (expandedSessions.has(sid)) expandedSessions.delete(sid);
            else expandedSessions.add(sid);
            renderPastSessions();
        };
    });

    pastSessionsList.querySelectorAll('button[data-delete-session]').forEach(btn => {
        btn.onclick = async () => {
            const sid = btn.dataset.deleteSession;
            if (!confirm(`Delete session ${sid}? Captures will remain (just unlinked).`)) return;
            try {
                const r = await fetch(`/api/sessions/${encodeURIComponent(sid)}?unlink_captures=true`, {method:'DELETE'});
                if (!r.ok) throw new Error(await readError(r));
                await refreshPastSessions();
                await refreshCaptures();
            } catch (e) { alert('Delete failed: ' + e.message); }
        };
    });

    // Per-capture actions inside the expanded list
    pastSessionsList.querySelectorAll('button[data-reveal-cap]').forEach(btn => {
        btn.onclick = (e) => {
            e.stopPropagation();
            revealCaptureFolder(btn.dataset.revealCap);
        };
    });
    pastSessionsList.querySelectorAll('button[data-edit-cap]').forEach(btn => {
        btn.onclick = (e) => {
            e.stopPropagation();
            openMetaModal(btn.dataset.editCap);
        };
    });
    pastSessionsList.querySelectorAll('button[data-unlink-cap]').forEach(btn => {
        btn.onclick = (e) => {
            e.stopPropagation();
            removeCaptureFromSession(btn.dataset.fromSession, btn.dataset.unlinkCap);
        };
    });
    pastSessionsList.querySelectorAll('button[data-add-to-session]').forEach(btn => {
        btn.onclick = (e) => {
            e.stopPropagation();
            const sid = btn.dataset.addToSession;
            const session = pastSessions.find(s => s.id === sid);
            if (session) openLinkModal(session);
        };
    });
}

pastSessionsToggle.onclick = () => {
    pastSessionsList.classList.toggle('hidden');
    pastSessionsToggle.textContent = pastSessionsList.classList.contains('hidden')
        ? '▸ Past sessions' : '▾ Past sessions';
    if (!pastSessionsList.classList.contains('hidden')) refreshPastSessions();
};

sessionStartBtn.onclick = () => openSessionModal();

function openSessionModal() {
    sessionModal.classList.add('open');
    sessionModal.setAttribute('aria-hidden', 'false');
    document.getElementById('sess-name').focus();
}
function closeSessionModal() {
    sessionModal.classList.remove('open');
    sessionModal.setAttribute('aria-hidden', 'true');
}
sessionModal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeSessionModal));
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && sessionModal.classList.contains('open')) closeSessionModal();
});

sessionForm.onsubmit = async (e) => {
    e.preventDefault();
    const body = {
        name:     document.getElementById('sess-name').value.trim(),
        subject:  document.getElementById('sess-subject').value.trim(),
        arm:      document.getElementById('sess-arm').value || null,
        gestures: document.getElementById('sess-gestures').value.split(',').map(s=>s.trim()).filter(Boolean),
        tags:     document.getElementById('sess-tags').value.split(',').map(s=>s.trim()).filter(Boolean),
        notes:    document.getElementById('sess-notes').value,
    };
    try {
        const r = await fetch('/api/sessions', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(await readError(r));
        // Clear the form for next time
        sessionForm.reset();
        closeSessionModal();
    } catch (e) {
        alert('Could not start session: ' + e.message);
    }
};

async function endSession() {
    if (!confirm(`End session "${activeSession ? (activeSession.name || activeSession.id) : ''}"?`)) return;
    try {
        const r = await fetch('/api/sessions/end', {method: 'POST'});
        if (!r.ok) throw new Error(await readError(r));
        await refreshPastSessions();
    } catch (e) {
        alert('Could not end session: ' + e.message);
    }
}

// ---------- recording UI ----------

function renderRecording() {
    if (recordingState) {
        recordBtn.textContent = '■ Stop recording';
        recordBtn.classList.add('recording');
        if (recordMultibandBtn) recordMultibandBtn.disabled = true;

        const r = recordingState;
        const rate = (r.match_rate ?? 0);
        const ratePct = (rate * 100).toFixed(1);
        let rateCls = 'match-rate-good';
        if (rate < 0.5)      rateCls = 'match-rate-bad';
        else if (rate < 0.9) rateCls = 'match-rate-warn';

        const label  = r.label_device_id  || '(none)';
        // Multi-band: show every tagged band; single-source: just the sensor.
        const sensorsMap = r.sensors || {};
        const sensorLine = Object.keys(sensorsMap).length > 1
            ? 'bands: <b>' + Object.entries(sensorsMap)
                .map(([id, role]) => `${escapeHtml(role)}:${escapeHtml(id)}`).join(', ') + '</b>'
            : `sensor: <b>${escapeHtml(r.sensor_device_id || '?')}</b>`;

        recordStatus.innerHTML = `
            <div>${escapeHtml(r.filename)} · ${r.rows} paired rows · ${r.duration_s}s · win ${r.window_ms ?? 100}ms</div>
            <div>${sensorLine} &nbsp; label: <b>${escapeHtml(label)}</b></div>
            <div>matched: ${r.matched ?? 0} / ${r.sensor_frames_seen ?? 0}
                 (<span class="${rateCls}">${ratePct}%</span>)
                 · unpaired sensor: ${r.unpaired_sensor ?? 0}
                 · label pkts: ${r.label_packets_seen ?? 0}</div>
        `;
    } else {
        recordBtn.textContent = '● Start recording';
        recordBtn.classList.remove('recording');
        if (recordMultibandBtn) recordMultibandBtn.disabled = false;
        recordStatus.textContent = '';
    }
}

async function readError(r) {
    // FastAPI returns errors in several shapes:
    //   {"detail": "msg"}                              (our HTTPException)
    //   {"detail": [{loc, msg, type, input}, ...]}     (Pydantic validation, 422)
    //   {"detail": {<anything>}}                       (rare)
    //   {raw text}                                     (Starlette default)
    let body;
    try {
        body = await r.clone().json();
    } catch {
        return (await r.text()) || `HTTP ${r.status}`;
    }
    const d = body.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
        return d.map(it => {
            const loc = Array.isArray(it.loc) ? it.loc.join('.') : '';
            return `${loc ? loc + ': ' : ''}${it.msg || JSON.stringify(it)}`;
        }).join('; ');
    }
    if (d && typeof d === 'object') return JSON.stringify(d);
    return JSON.stringify(body) || `HTTP ${r.status}`;
}

recordBtn.onclick = async () => {
    try {
        if (recordingState) {
            const r = await fetch('/api/recording', { method: 'DELETE' });
            if (!r.ok) throw new Error(await readError(r));
            await refreshCaptures();
        } else {
            // Map picker values to the API contract:
            //   ''         -> omit (let server auto-pick)
            //   '__none__' -> '' (explicit empty -> server disables pairing)
            //   '<id>'     -> '<id>' (explicit device pick)
            const sensorVal = sensorSelect.value;
            const labelVal  = labelSelect.value;
            const body = { filename: captureName.value.trim() || null };
            if (sensorVal === '__none__') {
                throw new Error('Sensor source can\'t be "none" -- need a flexgrid to record');
            }
            if (sensorVal) body.sensor_device_id = sensorVal;
            if (labelVal === '__none__') body.label_device_id = '';
            else if (labelVal)           body.label_device_id = labelVal;

            const r = await fetch('/api/recording', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!r.ok) throw new Error(await readError(r));
            captureName.value = '';
        }
    } catch (e) {
        alert(`Error: ${e.message}`);
    }
};

// Multi-band: record every flexgrid tagged left/right in the Sources panel
// plus the labeler. Start-only; use the main Stop button to stop.
if (recordMultibandBtn) {
    recordMultibandBtn.onclick = async () => {
        if (recordingState) return;
        try {
            const body = { filename: captureName.value.trim() || null };
            const r = await fetch('/api/recording/multiband', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!r.ok) throw new Error(await readError(r));
            captureName.value = '';
        } catch (e) {
            alert(`Error: ${e.message}`);
        }
    };
}

// ---------- captures list ----------

async function refreshCaptures() {
    try {
        const r = await fetch('/api/captures');
        const list = await r.json();
        renderCaptures(list);
    } catch (e) {
        console.warn('captures fetch failed', e);
    }
}

function captureIsInActiveSession(c) {
    if (!activeSession) return true;            // no filter
    const meta = c.meta;
    if (!meta) return false;
    // The active session_id lives in meta.auto.session_id, which the
    // list_captures summary doesn't expose by default; tags carry a
    // `session:<id>` tag we seed at recording time -- check both for
    // robustness.
    if ((meta.tags || []).some(t => t === 'session:' + activeSession.id)) return true;
    if (meta.session_id === activeSession.id) return true;       // future-proof
    return false;
}

function renderCaptures(list) {
    // Prune selection set down to captures that still exist
    const existing = new Set(list.map(c => c.name));
    for (const n of [...selectedCaptures]) {
        if (!existing.has(n)) selectedCaptures.delete(n);
    }

    if (!list.length) {
        capturesBody.innerHTML = '<tr class="empty"><td colspan="6">No captures saved yet.</td></tr>';
        updateSelectionStatus();
        return;
    }

    // When a session is active, dim captures that aren't part of it
    // rather than hiding them outright (less surprising; the operator
    // can still see the full history but the "current session" rows
    // pop visually).
    const rows = list.map(c => {
        const date = new Date(c.mtime * 1000).toLocaleString();
        const kb = (c.size_bytes / 1024).toFixed(1);
        const checked = selectedCaptures.has(c.name) ? 'checked' : '';
        const metaCell = renderCaptureMetaSummary(c.meta);
        const outsideSession = !captureIsInActiveSession(c);
        const trClass = outsideSession ? ' class="outside-session"' : '';
        return `<tr${trClass} data-name="${escapeHtml(c.name)}">
            <td class="captures-check"><input type="checkbox" class="cap-check" data-name="${escapeHtml(c.name)}" ${checked}></td>
            <td>${escapeHtml(c.name)}</td>
            <td>${metaCell}</td>
            <td>${kb} KB</td>
            <td>${escapeHtml(date)}</td>
            <td class="actions">
                <button class="link" data-edit="${escapeHtml(c.name)}">edit</button>
                <button class="link" data-reveal="${escapeHtml(c.name)}" title="Show this file in your file manager">📂</button>
                <a href="/api/captures/${encodeURIComponent(c.name)}/download" download>download</a>
                <button class="link danger" data-del="${escapeHtml(c.name)}">delete</button>
            </td>
        </tr>`;
    }).join('');
    capturesBody.innerHTML = rows;
    capturesBody.querySelectorAll('button[data-del]').forEach(btn => {
        btn.onclick = async () => {
            const name = btn.dataset.del;
            if (!confirm(`Delete ${name}?`)) return;
            const r = await fetch(`/api/captures/${encodeURIComponent(name)}`, { method: 'DELETE' });
            if (r.ok) refreshCaptures();
            else alert('Delete failed');
        };
    });
    capturesBody.querySelectorAll('input.cap-check').forEach(box => {
        box.onchange = () => {
            const name = box.dataset.name;
            if (box.checked) selectedCaptures.add(name);
            else selectedCaptures.delete(name);
            updateSelectionStatus();
        };
    });
    capturesBody.querySelectorAll('button[data-edit]').forEach(btn => {
        btn.onclick = () => openMetaModal(btn.dataset.edit);
    });
    capturesBody.querySelectorAll('button[data-reveal]').forEach(btn => {
        btn.onclick = () => revealCaptureFolder(btn.dataset.reveal);
    });
    updateSelectionStatus();
}

// Render the compact meta column on a capture row. Stays empty (italic
// 'no meta') until the user fills it in via the edit modal.
function renderCaptureMetaSummary(meta) {
    if (!meta) return '<span class="cap-meta-summary empty">— click edit to annotate</span>';
    const parts = [];
    if (meta.arm) {
        const cls = meta.arm === 'left' ? 'arm-left' : 'arm-right';
        parts.push(`<span class="${cls}">${escapeHtml(meta.arm)}</span>`);
    }
    if (meta.gesture) {
        parts.push(`<span class="gesture">${escapeHtml(meta.gesture)}</span>`);
    }
    if (meta.subject) {
        parts.push(escapeHtml(meta.subject));
    }
    const inline = parts.join(' · ');
    const tagBits = (meta.tags || []).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('');
    const noteFlag = meta.has_notes ? ' <span class="tag">📝 notes</span>' : '';
    const body = (inline || tagBits || noteFlag)
        ? `${inline}${tagBits ? ' ' + tagBits : ''}${noteFlag}`
        : '<span class="empty">— click edit to annotate</span>';
    return `<span class="cap-meta-summary">${body}</span>`;
}

// ---------- capture metadata modal ----------

const metaModal     = document.getElementById('meta-modal');
const metaForm      = document.getElementById('meta-form');
const metaNameEl    = document.getElementById('meta-name');
const metaArmEl     = document.getElementById('meta-arm');
const metaSubjectEl = document.getElementById('meta-subject');
const metaGestureEl = document.getElementById('meta-gesture');
const metaTagsEl    = document.getElementById('meta-tags');
const metaNotesEl   = document.getElementById('meta-notes');
const metaAutoEl    = document.getElementById('meta-auto');

let editingCaptureName = null;

async function openMetaModal(name) {
    editingCaptureName = name;
    metaNameEl.textContent = name;
    // Default the form fields to empty before fetching, so a slow fetch
    // doesn't show stale values from the previous capture briefly.
    metaArmEl.value = '';
    metaSubjectEl.value = '';
    metaGestureEl.value = '';
    metaTagsEl.value = '';
    metaNotesEl.value = '';
    metaAutoEl.textContent = '(loading...)';
    metaModal.classList.add('open');
    metaModal.setAttribute('aria-hidden', 'false');

    try {
        const r = await fetch(`/api/captures/${encodeURIComponent(name)}/meta`);
        if (!r.ok) throw new Error(await readError(r));
        const meta = await r.json();
        metaArmEl.value     = meta.arm || '';
        metaSubjectEl.value = meta.subject || '';
        metaGestureEl.value = meta.gesture || '';
        metaTagsEl.value    = Array.isArray(meta.tags) ? meta.tags.join(', ') : '';
        metaNotesEl.value   = meta.notes || '';
        const auto = meta.auto || {};
        metaAutoEl.textContent = Object.keys(auto).length
            ? JSON.stringify(auto, null, 2)
            : '(none -- this capture predates auto-seeding)';
    } catch (e) {
        metaAutoEl.textContent = '(error loading: ' + (e.message || e) + ')';
    }
}

function closeMetaModal() {
    metaModal.classList.remove('open');
    metaModal.setAttribute('aria-hidden', 'true');
    editingCaptureName = null;
}

metaModal.querySelectorAll('[data-close]').forEach(el => {
    el.addEventListener('click', closeMetaModal);
});
// Esc closes too
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && metaModal.classList.contains('open')) closeMetaModal();
});

metaForm.onsubmit = async (e) => {
    e.preventDefault();
    if (!editingCaptureName) return;
    const tagsRaw = metaTagsEl.value;
    const tags = tagsRaw
        .split(',')
        .map(t => t.trim())
        .filter(Boolean);
    const body = {
        arm:     metaArmEl.value || null,
        subject: metaSubjectEl.value.trim(),
        gesture: metaGestureEl.value.trim(),
        tags:    tags,
        notes:   metaNotesEl.value,
    };
    try {
        const r = await fetch(`/api/captures/${encodeURIComponent(editingCaptureName)}/meta`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(await readError(r));
        closeMetaModal();
        await refreshCaptures();   // re-render the row summary
    } catch (err) {
        alert('Save failed: ' + (err.message || err));
    }
};

function updateSelectionStatus() {
    const n = selectedCaptures.size;
    selStatus.textContent = `${n} selected`;
    trainBtn.disabled = (n === 0);
    // Sync check-all state: checked when ALL rows are selected, indeterminate
    // when some but not all are.
    const total = capturesBody.querySelectorAll('input.cap-check').length;
    checkAll.checked = (n > 0 && n === total);
    checkAll.indeterminate = (n > 0 && n < total);
}

checkAll.onchange = () => {
    const boxes = capturesBody.querySelectorAll('input.cap-check');
    boxes.forEach(b => {
        b.checked = checkAll.checked;
        const name = b.dataset.name;
        if (b.checked) selectedCaptures.add(name);
        else selectedCaptures.delete(name);
    });
    updateSelectionStatus();
};

// ---------- training ----------

trainBtn.onclick = async () => {
    if (selectedCaptures.size === 0) return;
    const captures = [...selectedCaptures];
    const label = captures.length === 1 ? captures[0] : `${captures.length} captures`;

    trainBtn.disabled = true;
    trainStatus.className = 'train-status busy';
    trainStatus.textContent = `⏳ Training on ${label}...`;

    try {
        const r = await fetch('/api/train', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ captures, activate: true }),
        });
        if (!r.ok) throw new Error(await readError(r));
        const result = await r.json();
        const m = result.metrics || {};
        const r2 = (m.r2 ?? 0).toFixed(3);
        const mse = (m.mse ?? 0).toFixed(4);
        const nf  = m.n_features ?? '?';
        const nl  = m.n_labels ?? '?';
        const nt  = m.n_train ?? '?';
        // `active` from the API now means "loaded into engine", NOT "running".
        // Inference stays paused on a fresh load -- operator clicks ▶ to run.
        const loaded = result.active ? ' [loaded · click ▶ Resume to run]' : '';
        trainStatus.className = 'train-status ok';
        trainStatus.textContent = `✓ Trained on ${nt} rows · ${nf} features → ${nl} labels · R²=${r2} · MSE=${mse}${loaded}`;
        await refreshModels();
    } catch (e) {
        trainStatus.className = 'train-status error';
        trainStatus.textContent = `✗ Train failed: ${e.message}`;
    } finally {
        trainBtn.disabled = (selectedCaptures.size === 0);
    }
};

// ---------- models panel ----------

async function refreshModels() {
    try {
        const r = await fetch('/api/models');
        if (!r.ok) return;
        const list = await r.json();
        renderModels(list);
    } catch (e) {
        // best-effort
    }
}

function renderModels(list) {
    modelsCount.textContent = `${list.length} model${list.length === 1 ? '' : 's'}`;
    if (!list.length) {
        modelsBody.innerHTML = '<tr class="empty"><td colspan="6">No models trained yet.</td></tr>';
        return;
    }
    modelsBody.innerHTML = list.map(m => {
        const metrics = m.metrics || {};
        const r2  = (metrics.r2 ?? null);
        const mse = (metrics.mse ?? null);
        const nf  = metrics.n_features ?? '?';
        const nl  = metrics.n_labels ?? '?';
        const r2s  = (r2 !== null && !isNaN(r2)) ? Number(r2).toFixed(3) : '—';
        const mses = (mse !== null && !isNaN(mse)) ? Number(mse).toFixed(4) : '—';
        const created = m.created ?? '';
        const activeBadge = m.active ? '<span class="badge-active">active</span>' : '';
        const escName = escapeHtml(m.name || '');
        const escPath = escapeHtml(m.path || '');
        return `<tr>
            <td>${escName} ${activeBadge}</td>
            <td>${escapeHtml(created)}</td>
            <td>${r2s}</td>
            <td>${mses}</td>
            <td>${nf} × ${nl}</td>
            <td class="actions">
                ${m.active ? '' :
                  `<button class="link" data-activate="${escPath}">use</button>`}
            </td>
        </tr>`;
    }).join('');
    modelsBody.querySelectorAll('button[data-activate]').forEach(btn => {
        btn.onclick = async () => {
            const path = btn.dataset.activate;
            try {
                const r = await fetch('/api/inference/model', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path }),
                });
                if (!r.ok) throw new Error(await readError(r));
                await refreshModels();
            } catch (e) {
                alert(`Activate failed: ${e.message}`);
            }
        };
    });
}

// ---------- LASK5 piston bars + joystick ----------

// LASK5 piston values come in two shapes depending on firmware:
//   - monolithic (boot.py): calibrated normalized 0.0..1.0 floats
//   - modular / future / raw: raw ADC ints in 0..4095
// We auto-detect per value: anything in [0, 1] is treated as a fraction;
// anything larger is treated as raw ADC and divided by 4095.
const LASK_ADC_MAX = 4095;
const laskMeta = document.getElementById('lask-meta');
const laskBars = document.getElementById('lask-bars');
const joyCanvas = document.getElementById('joystick-canvas');
const joyCtx = joyCanvas.getContext('2d');
const joyVals = document.getElementById('joystick-vals');

function pistonFraction(v) {
    if (typeof v !== 'number' || !isFinite(v)) return 0;
    // Normalized 0..1 floats land here exactly (and so do clean integer 0/1
    // values, which we still want to render as 0% / 100% rather than 0.024%).
    const frac = (v <= 1) ? v : (v / LASK_ADC_MAX);
    return Math.max(0, Math.min(1, frac));
}

function pistonValText(v) {
    if (typeof v !== 'number' || !isFinite(v)) return '--';
    // Floats: 2 decimals. Ints (and anything > 1): show as integer.
    return (v <= 1 && v !== Math.floor(v)) ? v.toFixed(2) : String(v);
}

function renderLask(dev) {
    if (!dev || !Array.isArray(dev.values) || dev.values.length === 0) {
        laskMeta.textContent = 'no device';
        // zero the bars
        laskBars.querySelectorAll('.piston').forEach(p => {
            p.querySelector('.piston-fill').style.height = '0%';
            p.querySelector('.piston-val').textContent = '--';
        });
        drawJoystick(null);
        return;
    }
    laskMeta.textContent =
        `${dev.device_id} · ${dev.hz.toFixed(1)} Hz · ${dev.packets} pkts`;
    const vals = dev.values;
    laskBars.querySelectorAll('.piston').forEach(p => {
        const i = parseInt(p.dataset.i, 10);
        const v = i < vals.length ? vals[i] : 0;
        const pct = pistonFraction(v) * 100;
        p.querySelector('.piston-fill').style.height = pct.toFixed(1) + '%';
        p.querySelector('.piston-val').textContent = pistonValText(v);
    });
    drawJoystick(dev.joystick);
}

function drawJoystick(j) {
    const w = joyCanvas.width, h = joyCanvas.height;
    joyCtx.clearRect(0, 0, w, h);
    // crosshair
    joyCtx.strokeStyle = '#2a2f3e';
    joyCtx.beginPath();
    joyCtx.moveTo(w / 2, 0); joyCtx.lineTo(w / 2, h);
    joyCtx.moveTo(0, h / 2); joyCtx.lineTo(w, h / 2);
    joyCtx.stroke();
    // perimeter
    joyCtx.strokeStyle = '#1d2230';
    joyCtx.strokeRect(0.5, 0.5, w - 1, h - 1);
    if (!j || typeof j.x !== 'number' || typeof j.y !== 'number') {
        joyVals.textContent = '--, --';
        return;
    }
    // Map 0..4095 to 0..w / 0..h. Y axis: invert so up = up on screen.
    const x = (j.x / 4095) * w;
    const y = h - (j.y / 4095) * h;
    joyCtx.fillStyle = '#ff337b';
    joyCtx.beginPath();
    joyCtx.arc(x, y, 5, 0, Math.PI * 2);
    joyCtx.fill();
    joyVals.textContent = `${j.x}, ${j.y}`;
}

// ---------- ML inference (predicted LASK) ----------

const inferenceMeta   = document.getElementById('inference-meta');
const inferenceBars   = document.getElementById('inference-bars');
const inferToggleBtn  = document.getElementById('infer-toggle');
const inferHandInput  = document.getElementById('infer-hand');
const inferHandApply  = document.getElementById('infer-hand-apply');
const inferHandState  = document.getElementById('infer-hand-state');

// Don't blast user input every WS tick. We only sync the input from the
// server when it changes AND the field isn't currently focused (so we
// don't yank text out from under their cursor).
let lastSnapshotHand = undefined;

function renderInference(inf) {
    // Controls state (button + hand input) regardless of bars
    renderInferenceControls(inf);

    // REC+LIVE badge appears when BOTH a recording is in progress AND
    // inference is running. This is the "proof of life" signal -- you
    // can watch the prediction bars track the LASK5 ground-truth bars
    // in real time while the recording writes the paired rows.
    const recLiveOn = !!(recordingState && inf && inf.available);
    const recLiveSpan = (recLiveOn ? ' <span class="rec-live-badge">REC + LIVE</span>' : '');

    if (!inf) {
        inferenceMeta.innerHTML = 'no model loaded' + recLiveSpan;
        return;
    }
    if (!inf.available || !Array.isArray(inf.piston_values)) {
        inferenceMeta.innerHTML = escapeHtml(inf.status || 'no model loaded') + recLiveSpan;
        inferenceBars.classList.add('dimmed');
        inferenceBars.querySelectorAll('.piston').forEach(p => {
            p.querySelector('.piston-fill').style.height = '0%';
            p.querySelector('.piston-val').textContent = '--';
        });
        return;
    }
    inferenceBars.classList.remove('dimmed');
    inferenceMeta.innerHTML = escapeHtml(inf.model || 'live') + recLiveSpan;
    const vals = inf.piston_values;
    inferenceBars.querySelectorAll('.piston').forEach(p => {
        const i = parseInt(p.dataset.i, 10);
        const v = i < vals.length ? vals[i] : 0;
        const pct = pistonFraction(v) * 100;
        p.querySelector('.piston-fill').style.height = pct.toFixed(1) + '%';
        p.querySelector('.piston-val').textContent = pistonValText(v);
    });
}

// One-shot: if the server has no hand_target on first snapshot but we have
// one saved in localStorage, auto-apply it so launching `openmuscle web`
// doesn't lose the address every time. UDP-only (the only protocol we
// support); port defaults to 3145.
let handTargetRestoreAttempted = false;
function maybeRestoreHandTarget(inf) {
    if (handTargetRestoreAttempted) return;
    if (!inf) return;                           // wait for first inference snapshot
    handTargetRestoreAttempted = true;          // one-shot regardless of outcome
    if (inf.hand_target) return;                // server already has one (e.g. --hand on CLI)
    const saved = localStorage.getItem(STORE_HAND);
    if (!saved) return;
    autoApplyHandTarget(saved);
}

async function autoApplyHandTarget(raw) {
    let host = raw, port = 3145;
    if (raw.includes(':')) {
        const idx = raw.lastIndexOf(':');
        host = raw.slice(0, idx);
        const portN = parseInt(raw.slice(idx + 1), 10);
        if (Number.isFinite(portN) && portN > 0 && portN < 65536) port = portN;
    }
    try {
        await fetch('/api/inference/hand', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ host, port }),
        });
    } catch (e) {
        console.warn('hand target auto-restore failed', e);
    }
}

function renderInferenceControls(inf) {
    maybeRestoreHandTarget(inf);

    const hasModel = !!(inf && inf.model);
    const enabled  = !!(inf && inf.enabled);

    // --- toggle button ---
    inferToggleBtn.disabled = !hasModel;
    inferToggleBtn.classList.toggle('running', enabled);
    inferToggleBtn.classList.toggle('paused', hasModel && !enabled);
    if (!hasModel)      inferToggleBtn.textContent = '▶ Start';
    else if (enabled)   inferToggleBtn.textContent = '⏸ Pause';
    else                inferToggleBtn.textContent = '▶ Resume';
    inferToggleBtn.title = hasModel
        ? (enabled ? 'Click to pause inference' : 'Click to resume inference')
        : 'Load a model from the Models panel first';

    // --- hand target input ---
    const hand = (inf && inf.hand_target) || '';
    if (hand !== lastSnapshotHand) {
        lastSnapshotHand = hand;
        if (document.activeElement !== inferHandInput) {
            inferHandInput.value = hand;
        }
    }
    if (hand) {
        inferHandState.className = 'sel-status active';
        inferHandState.textContent = '● forwarding';
    } else {
        inferHandState.className = 'sel-status';
        inferHandState.textContent = 'no hand target';
    }
}

// ---- toggle inference on/off ----
inferToggleBtn.onclick = async () => {
    if (inferToggleBtn.disabled) return;
    const wantEnabled = !inferToggleBtn.classList.contains('running');
    try {
        const r = await fetch('/api/inference/enabled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: wantEnabled }),
        });
        if (!r.ok) throw new Error(await readError(r));
    } catch (e) {
        alert(`Could not ${wantEnabled ? 'resume' : 'pause'}: ${e.message}`);
    }
};

// ---- apply hand target ----
async function applyHandTarget() {
    const raw = inferHandInput.value.trim();
    let host = null;
    let port = 3145;
    if (raw) {
        // Accept "host" or "host:port"
        if (raw.includes(':')) {
            const idx = raw.lastIndexOf(':');
            host = raw.slice(0, idx);
            const portStr = raw.slice(idx + 1);
            const portN = parseInt(portStr, 10);
            if (!Number.isFinite(portN) || portN < 1 || portN > 65535) {
                alert(`Bad port: ${portStr}`);
                return;
            }
            port = portN;
        } else {
            host = raw;
        }
    }
    try {
        const r = await fetch('/api/inference/hand', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ host, port }),
        });
        if (!r.ok) throw new Error(await readError(r));
        // Persist so next launch auto-restores. Clear on explicit empty
        // so the operator can "forget" the target deliberately.
        if (host) localStorage.setItem(STORE_HAND, raw);
        else      localStorage.removeItem(STORE_HAND);
        // Force the snapshot side to refresh by clearing the cache so the
        // next tick syncs the (possibly normalized) value back into the input.
        lastSnapshotHand = undefined;
    } catch (e) {
        alert(`Could not set hand target: ${e.message}`);
    }
}

inferHandApply.onclick = applyHandTarget;
inferHandInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') applyHandTarget();
});

// ---------- Studio shell: comparator residuals (Δ) ----------

// Compute per-piston residual (predicted - ground_truth) and write it into
// the .delta-row elements in the comparator. Color-codes by direction so
// the operator can see at a glance whether the model is over- or under-
// shooting each finger.
//
// CLOSE_THRESHOLD picked at 0.05 (5% of the 0..1 scale) — below that, the
// difference is below the noise floor of the LASK5 measurement itself.
const RESIDUAL_CLOSE_THRESHOLD = 0.05;

function renderResiduals(laskDev, inf) {
    const deltaRows = document.querySelectorAll('#comparator-deltas .delta-row');
    if (!deltaRows.length) return;
    const gt   = laskDev && Array.isArray(laskDev.values) ? laskDev.values : null;
    const pred = inf && Array.isArray(inf.piston_values)  ? inf.piston_values : null;

    deltaRows.forEach((row, i) => {
        const valEl = row.querySelector('.delta-val');
        row.classList.remove('over', 'under', 'close');
        if (!gt || !pred || i >= gt.length || i >= pred.length) {
            if (valEl) valEl.textContent = '--';
            return;
        }
        const g = pistonFraction(gt[i]);
        const p = pistonFraction(pred[i]);
        const d = p - g;
        valEl.textContent = (d >= 0 ? '+' : '') + d.toFixed(2);
        if (Math.abs(d) < RESIDUAL_CLOSE_THRESHOLD) row.classList.add('close');
        else if (d > 0)                              row.classList.add('over');
        else                                          row.classList.add('under');
    });
}

// ---------- Studio shell: top-bar pipeline status strip ----------

// Set a pipe-pill's status + value text. State controls colour:
//   'live'  -- blue accent (data flowing)
//   'ok'    -- green (idle but healthy)
//   'warn'  -- orange
//   'bad'   -- red
//   ''      -- neutral grey
function setPipePill(id, state, valText) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('ok', 'warn', 'bad', 'live');
    if (state) el.classList.add(state);
    const valEl = el.querySelector('.pipe-val');
    if (valEl) valEl.textContent = valText;
}

function renderPipelinePills(msg, laskDev) {
    // SENSOR pill = the active flexgrid (the one driving the heatmap)
    const dev = selectedDevice();
    if (dev && dev.device_type === 'flexgrid') {
        const stale = dev.last_seen_age > 2.0;
        setPipePill('pipe-sensor', stale ? 'warn' : 'live', `${dev.hz.toFixed(0)}Hz`);
    } else {
        setPipePill('pipe-sensor', '', '--');
    }

    // LABEL pill = LASK5 stream
    if (laskDev) {
        const stale = laskDev.last_seen_age > 2.0;
        setPipePill('pipe-label', stale ? 'warn' : 'live', `${laskDev.hz.toFixed(0)}Hz`);
    } else {
        setPipePill('pipe-label', '', '--');
    }

    // CAPTURE pill
    if (recordingState) {
        const matchRate = recordingState.match_rate ?? 0;
        const cls = matchRate < 0.5 ? 'bad' : (matchRate < 0.9 ? 'warn' : 'live');
        setPipePill('pipe-capture', cls, `REC ${recordingState.rows ?? 0}r`);
    } else if (activeSession) {
        setPipePill('pipe-capture', 'ok', `session: ${activeSession.name || activeSession.id}`);
    } else {
        setPipePill('pipe-capture', '', 'idle');
    }

    // MODEL pill
    const inf = msg.inference;
    if (inf && inf.model && inf.enabled)        setPipePill('pipe-model', 'live', inf.model);
    else if (inf && inf.model && !inf.enabled)  setPipePill('pipe-model', 'ok', inf.model + ' (paused)');
    else                                         setPipePill('pipe-model', '', 'none');

    // HAND pill = UDP forwarding target
    if (inf && inf.hand_target) setPipePill('pipe-hand', 'live', inf.hand_target);
    else                        setPipePill('pipe-hand', '', 'off');
}

// ---------- Studio shell: diagnostics drawer ----------

const diagToggle = document.getElementById('diag-toggle');
const diagBody   = document.getElementById('diag-body');
if (diagToggle && diagBody) {
    diagToggle.onclick = () => {
        const isHidden = diagBody.classList.toggle('hidden');
        diagToggle.setAttribute('aria-expanded', isHidden ? 'false' : 'true');
        diagToggle.textContent = (isHidden ? '▸' : '▾') + ' Diagnostics & logs';
        // Logs poll runs unconditionally; we just hide the DOM. Cheap.
    };
}

// ---------- utils ----------

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

// ---------- logs panel ----------

const logList         = document.getElementById('log-list');
const logLevelFilter  = document.getElementById('log-level-filter');
const logClearBtn     = document.getElementById('log-clear-btn');
const logAutoscroll   = document.getElementById('log-autoscroll');

// Last log id we've seen from the server. Polling sends ?since=N so we
// only fetch entries we haven't already rendered.
let lastLogId = 0;
// Local mirror of received entries so filters can re-render without
// re-fetching. Capped to avoid unbounded DOM growth.
let logEntries = [];
const LOG_LOCAL_CAP = 500;

async function refreshLogs() {
    try {
        const r = await fetch(`/api/logs?since=${lastLogId}`);
        if (!r.ok) return;
        const body = await r.json();
        const fresh = body.entries || [];
        if (!fresh.length && lastLogId !== 0) return;
        if (fresh.length) {
            logEntries.push(...fresh);
            if (logEntries.length > LOG_LOCAL_CAP) {
                logEntries.splice(0, logEntries.length - LOG_LOCAL_CAP);
            }
            lastLogId = body.latest_id ?? fresh[fresh.length - 1].id;
        } else {
            lastLogId = body.latest_id ?? lastLogId;
        }
        renderLogs();
    } catch (e) {
        // best-effort polling
    }
}

function renderLogs() {
    const filter = logLevelFilter.value;  // '', 'WARN', 'ERROR'
    const filtered = logEntries.filter(e => {
        if (!filter) return true;
        if (filter === 'WARN')  return e.level === 'WARNING' || e.level === 'WARN' || e.level === 'ERROR';
        if (filter === 'ERROR') return e.level === 'ERROR' || e.level === 'CRITICAL';
        return true;
    });
    if (!filtered.length) {
        logList.innerHTML = '<div class="log-empty">No log entries match the current filter.</div>';
        return;
    }
    const wasAtBottom = logAutoscroll.checked
        ? (logList.scrollTop + logList.clientHeight >= logList.scrollHeight - 10)
        : false;
    logList.innerHTML = filtered.map(e => {
        const ts = formatLogTs(e.t);
        const lvl = (e.level || 'INFO').toUpperCase();
        const lvlCls = 'lvl-' + lvl.toLowerCase().replace('warning', 'warn');
        return `<div class="log-row ${lvlCls}">
            <span class="log-ts">${escapeHtml(ts)}</span>
            <span class="log-level">${escapeHtml(lvl)}</span>
            <span class="log-source">${escapeHtml(e.source || '-')}</span>
            <span class="log-message">${escapeHtml(e.message || '')}</span>
        </div>`;
    }).join('');
    if (logAutoscroll.checked || wasAtBottom) {
        logList.scrollTop = logList.scrollHeight;
    }
}

function formatLogTs(unixSec) {
    if (typeof unixSec !== 'number') return '';
    const d = new Date(unixSec * 1000);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    const ms = String(d.getMilliseconds()).padStart(3, '0');
    return `${hh}:${mm}:${ss}.${ms.slice(0, 2)}`;
}

logLevelFilter.onchange = renderLogs;
logClearBtn.onclick = () => {
    // Clears only the local view; the server keeps its ring buffer so a
    // refresh restores history.
    logEntries = [];
    renderLogs();
};

// Refresh captures + models + logs + past sessions on load and periodically
refreshCaptures();
refreshModels();
refreshLogs();
refreshPastSessions();
setInterval(refreshCaptures, 5000);
setInterval(refreshModels, 10000);
setInterval(refreshLogs, 2000);   // logs are the most "real-time" panel
setInterval(refreshPastSessions, 15000);

connectWS();

// OpenMuscle Web UI — live heatmap, recording, capture management.
// Single-page vanilla JS. No bundler, no framework.

const wsStatus    = document.getElementById('ws-status');
// #device-list is gone (Tory: the standalone Devices column was redundant);
// band cards next to each heatmap + label status lines replaced it.
const recordBtn   = document.getElementById('record-btn');
// The dedicated multiband/bilateral buttons are gone (one Record button whose
// mode is derived from what's streaming); the consts stay so the existing
// `if (recordMultibandBtn)` guards keep no-oping.
const recordMultibandBtn = document.getElementById('record-multiband-btn');
const recordBilateralBtn = document.getElementById('record-bilateral-btn');
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
// Sticky control bar (Tory's UI-overhaul: one Record button + derived plan,
// always-visible Predict, compact session chip)
const controlBar          = document.getElementById('control-bar');
const labelSourceSelect   = document.getElementById('label-source-select');
const recordPlanEl        = document.getElementById('record-plan');
const recordAdvancedEl    = document.getElementById('record-advanced');
const recordAdvancedToggle= document.getElementById('record-advanced-toggle');
const recordWindowInput   = document.getElementById('record-window');
const sessionChip         = document.getElementById('session-chip');
const sessionChipNew      = document.getElementById('session-chip-new');
const predictModels       = document.getElementById('predict-models');

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
const STORE_LABEL_SOURCE = 'om.label_source';   // vr | lask5 | none (Record plan input)

// Set of capture filenames currently checked in the table
const selectedCaptures = new Set();

let selectedDeviceId = null;
let lastDevices = [];
let recordingState = null;        // null when idle; {filename, rows, duration_s} when recording
let activeSession = null;          // null when no session active; {id, name, arm, ...} otherwise
let inferenceState = null;         // last inference snapshot, used for REC+LIVE detection
let debugMode = false;             // GET /api/mode -> unlock the Debug section
let debugFreeze = false;           // pause the raw-frame inspector for reading

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
        // means the row list may show new session_id tags) AND models (the
        // Models panel scopes to the active session, like captures do).
        refreshCaptures();
        refreshModels();
    }
    // Engine slots changed (train/activate/auto-restore) -> re-badge the
    // Models panel now rather than waiting for the 10s poll, so "active"
    // chips always reflect what the router is really running.
    const amSig = JSON.stringify((inferenceState && inferenceState.active_models) || null);
    if (amSig !== _lastActiveModelsSig) {
        _lastActiveModelsSig = amSig;
        refreshModels();
    }
    renderActiveSession();
    // Draw a pressure grid for EVERY streaming flexgrid (both hands at once in a
    // two-hand session), not just the selected one (board #0304). drawHeatmaps
    // builds the band-row scaffold FIRST so renderBandCards can fill the card
    // to the left of each heatmap (Tory's redesign: no standalone Devices
    // column; device info lives with the visualization).
    drawHeatmaps();
    renderBandCards(msg.discovery || []);
    renderAvailableSources(msg.discovery || []);
    renderRecordPickers();
    renderRecording();
    renderRecordPlan();
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
    renderHandViewer(lastDevices.filter(d => d.device_type === 'quest_hand'),
                     msg.inference);
    // Label-device status lines under each visualization (quest / lask5 /
    // gamepad), replacing their old Devices-column entries.
    renderLabelStatusLines(lask);
    // IMU orientation widget: drive from a device carrying the fast data.imu
    // (prefer the selected device; else the first with imu).
    renderImuViewer();
    // Debug section (only when the server is in --debug mode).
    if (debugMode) renderDebugPanel();
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
    // Re-home the single widget under the band card of the flexgrid it is
    // showing (Tory's redesign: device info lives WITH the visualization).
    // appendChild MOVES the node, keeping the WebGL canvas alive; we only
    // touch the DOM when the target slot actually changes. Fallback: no band
    // row yet (imu frames before the first matrix frame) parks it in the dock
    // at the bottom of the heatmap hero.
    let slot = null;
    document.querySelectorAll('#heatmap-grids .band-row').forEach(row => {
        if (row.dataset.id === dev.device_id) slot = row.querySelector('.imu-slot');
    });
    if (!slot) slot = document.getElementById('imu-dock');
    if (slot && wrap.parentElement !== slot) slot.appendChild(wrap);
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
// a quest-trained model (>= 25 joints * 7 floats) is running. Toggles
// .has-hands on .comparator (CSS shows the viewer; the LASK5 pistons hide
// only when no LASK5 is streaming, see renderLask's .has-lask).
function renderHandViewer(questDevs, inference) {
    const comparator = document.querySelector('.comparator');
    const liveGrid = document.querySelector('.live-grid');
    const viewerReady = window.OMHandViewer && window.OMHandViewer.isReady;
    if (!questDevs || !questDevs.length) {
        if (comparator) comparator.classList.remove('has-hands');
        if (liveGrid) liveGrid.classList.remove('hand-mode');
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

    // .has-hands coexists with .has-lask (renderLask): both panels show when
    // both sources stream. The grid mirror widens the comparator column so
    // two 3D hands get real width.
    if (comparator) comparator.classList.add('has-hands');
    if (liveGrid) liveGrid.classList.add('hand-mode');
    window.OMHandViewer.setVisible(true);

    // BOTH hands (Tory, Clark session): route each quest device to its side's
    // viewer slot. A hand that stopped updating (tracking lost / left the view)
    // is hidden rather than frozen -- a stale skeleton reads as "still fine".
    const byDev = (inference && inference.by_device) || {};
    const metaBits = [];
    for (const side of ['left', 'right']) {
        const q = questDevs.find(d =>
            String(d.device_id).toLowerCase().includes(side));
        const fresh = q && (q.last_seen_age == null || q.last_seen_age < 2.0);
        const realFlat = (fresh && Array.isArray(q.values)
                          && q.values.length >= 25 * 7) ? q.values : null;
        // Predicted ghost for this side = the prediction of the band TAGGED
        // this side (separate-model-per-hand routing, same as the VR ghosts).
        let predFlat = null;
        const band = lastDevices.find(d =>
            d.device_type === 'flexgrid' && d.role === side);
        const pv = band && byDev[band.device_id];
        if (Array.isArray(pv) && pv.length >= 25 * 7) predFlat = pv;
        window.OMHandViewer.updateHand(side, realFlat, predFlat);
        if (realFlat) {
            metaBits.push(`${side[0].toUpperCase()} ${q.hz?.toFixed?.(0) || 0} Hz${predFlat ? ' +ghost' : ''}`);
        }
    }
    // Single-hand fallback: an unsided quest device (e.g. plain "quest-hand")
    // drives the right slot with the shared-model prediction, as before.
    if (!metaBits.length) {
        const q = questDevs[0];
        const pv = inference && inference.piston_values;
        window.OMHandViewer.update(
            Array.isArray(q.values) ? q.values : null,
            (Array.isArray(pv) && pv.length >= 25 * 7) ? pv : null);
        metaBits.push(`${q.hz?.toFixed?.(0) || 0} Hz`);
    }

    const gtMeta = document.getElementById('lask-meta');
    if (gtMeta) gtMeta.textContent = `Quest hands · ${metaBits.join(' · ')}`;
}

// ---------- label-device status lines ----------
//
// Tory's redesign: label devices lost their Devices-column entries; their
// info now sits UNDER their own visualization instead. Quest hands under the
// hand-viewer legend, LASK5 under the piston cluster, gamepad beside
// #gamepad-panel (that panel's internals belong to gamepad.js; ours is a
// sibling). Plain text, so the per-tick write-on-change gate is enough.

function setStatusLine(id, text) {
    const el = document.getElementById(id);
    if (!el || el._txt === text) return;   // skip DOM writes when unchanged
    el._txt = text;
    el.textContent = text;                 // empty text collapses via CSS :empty
}

function renderLabelStatusLines(lask) {
    const quests = lastDevices.filter(d => d.device_type === 'quest_hand');
    setStatusLine('hand-status-line', quests
        .map(q => `${q.device_id} ${(q.hz ?? 0).toFixed(0)} Hz`).join(' | '));

    let laskTxt = '';
    if (lask) {
        // Same ?? 0 guard as the quest line above: one field missing on one
        // tick must not kill the whole render loop.
        const bits = [lask.device_id, `${(lask.hz ?? 0).toFixed(0)} Hz`];
        const s = lask.status || {};
        if (typeof s.vbat === 'number') bits.push(`${s.vbat.toFixed(1)}V`);
        laskTxt = bits.join(' | ');
    }
    setStatusLine('lask-status-line', laskTxt);

    const pad = lastDevices.find(d => d.device_type === 'gamepad');
    setStatusLine('gamepad-status-line',
        pad ? `${pad.device_id} | ${(pad.hz ?? 0).toFixed(0)} Hz` : '');
}

// ---------- native V4 discovery: band cards + available sources ----------
//
// Tory's redesign: the standalone Devices column was redundant, so device
// info now lives WITH each visualization. Streaming flexgrids get a card to
// the LEFT of their own heatmap (renderBandCards fills the row scaffold that
// drawHeatmaps builds); discovered sources with no heatmap yet stay as
// compact "available" cards below the band rows (renderAvailableSources).
// Both preserve the old renderDiscovery signature-gating: rebuilding ~5x/sec
// destroyed the role <select> mid-interaction (that bug class burned us
// twice), so the DOM only rebuilds when meaningful state changes and the
// fast values refresh in place.

let _discoveryProbeWired = false;

// Shared control wiring: role <select> + subscribe/unsubscribe buttons carry
// the exact semantics of the old Sources rail (POST /api/discovery/role and
// /api/discovery/subscribe|unsubscribe; the next WS tick re-renders truth).
function wireRoleSelects(root) {
    root.querySelectorAll('.src-role-sel').forEach(sel => {
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
}

function wireSubButtons(root) {
    root.querySelectorAll('.src-btn').forEach(btn => {
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

// Shared role <select> markup (band card + available card).
function roleSelectHtml(d) {
    return `<label class="src-role">role
        <select class="src-role-sel" data-id="${escapeHtml(d.device_id)}">
            <option value=""${d.role ? '' : ' selected'}>untagged</option>
            <option value="left"${d.role === 'left' ? ' selected' : ''}>left</option>
            <option value="right"${d.role === 'right' ? ' selected' : ''}>right</option>
            <option value="labeler"${d.role === 'labeler' ? ' selected' : ''}>labeler</option>
        </select>
    </label>`;
}

// ---- band cards: the flexgrid square to the LEFT of its own heatmap ----

let _bandCardsSig = null;   // structural state; fast values refresh in place

function renderBandCards(discovery) {
    const grids = document.getElementById('heatmap-grids');
    if (!grids) return;
    const rows = [...grids.querySelectorAll('.band-row')];
    if (!rows.length) { _bandCardsSig = null; return; }
    const discById = {};
    discovery.forEach(d => { discById[d.device_id] = d; });
    const devById = {};
    lastDevices.forEach(d => { devById[d.device_id] = d; });

    // Structural signature: the controls rebuild ONLY when subscribe / role /
    // error / selection state changes, so an open role dropdown survives
    // ticks (same sacred gate as the old renderDiscovery).
    const sig = JSON.stringify(rows.map(row => {
        const disc = discById[row.dataset.id];
        return [row.dataset.id, row.dataset.id === selectedDeviceId,
                disc ? [disc.role || '', !!disc.subscribed, disc.sub_error || ''] : null];
    }));
    if (sig !== _bandCardsSig) {
        _bandCardsSig = sig;
        rows.forEach(row => {
            const id = row.dataset.id;
            const disc = discById[id];
            const card = row.querySelector('.band-card');
            if (!card) return;
            // State badge mirrors the old Sources rail. A band streaming
            // without a discovery entry (legacy push firmware) reads
            // "streaming" and gets no subscribe/role controls, because the
            // role + subscribe APIs are keyed to discovery entries.
            let stateCls = 'known', stateTxt = 'streaming';
            let controls = '';
            let errLine = '';
            if (disc) {
                if (disc.subscribed) { stateCls = 'subscribed'; stateTxt = 'subscribed'; }
                else if (disc.sub_error) { stateCls = 'err'; stateTxt = 'error'; }
                else { stateTxt = 'known'; }
                if (disc.sub_error) {
                    errLine = `<div class="src-err" title="${escapeHtml(disc.sub_error)}">${escapeHtml(disc.sub_error)}</div>`;
                }
                const btnAct = disc.subscribed ? 'unsubscribe' : 'subscribe';
                const btnTxt = disc.subscribed ? 'Unsubscribe' : 'Subscribe';
                controls = roleSelectHtml(disc)
                    + `<button class="src-btn" data-act="${btnAct}" data-id="${escapeHtml(id)}">${btnTxt}</button>`;
            }
            card.classList.toggle('subscribed', !!(disc && disc.subscribed));
            card.classList.toggle('err', !!(disc && disc.sub_error && !disc.subscribed));
            card.classList.toggle('selected', id === selectedDeviceId);
            // .band-meta / .band-stats stay control-free: they refresh per
            // tick below without risking an eaten click.
            card.innerHTML = `
                <div class="src-top">
                    <span class="src-id">${escapeHtml(id)}</span>
                    <span class="src-state ${stateCls}">${stateTxt}</span>
                </div>
                ${errLine}
                <div class="band-meta"></div>
                <div class="band-stats"></div>
                ${controls}`;
            // Clicking the card selects the band (drives the Orientation
            // widget + debug inspector); controls keep their own clicks.
            card.onclick = (e) => {
                if (e.target.closest('select, button, label')) return;
                selectedDeviceId = id;
                rows.forEach(r => {
                    const c = r.querySelector('.band-card');
                    if (c) c.classList.toggle('selected', r.dataset.id === selectedDeviceId);
                });
            };
        });
        wireRoleSelects(grids);
        wireSubButtons(grids);
    }

    // Fast values (hz / battery / rssi / age) refresh in place every tick;
    // writing only on change keeps the 5Hz tick cheap.
    rows.forEach(row => {
        const d = devById[row.dataset.id];
        const meta = row.querySelector('.band-meta');
        const stats = row.querySelector('.band-stats');
        if (!d || !meta || !stats) return;
        const stale = d.last_seen_age > 2.0;
        const metaHtml = `<span class="shape">${d.rows}×${d.cols}</span>`
            + ` <span class="hz">${d.hz.toFixed(1)} Hz</span>`
            + ` <span class="age${stale ? ' stale' : ''}">${stale ? `${d.last_seen_age.toFixed(1)}s` : 'live'}</span>`;
        if (meta._html !== metaHtml) { meta._html = metaHtml; meta.innerHTML = metaHtml; }
        const statsHtml = renderDeviceStatus(d);
        if (stats._html !== statsHtml) { stats._html = statsHtml; stats.innerHTML = statsHtml; }
    });
}

// ---- available sources: discovered but not streaming a heatmap yet ----

let _discoverySig = null;   // last-rendered state; skip rebuilds when unchanged

function renderAvailableSources(discovery) {
    const list = document.getElementById('discovery-list');
    const count = document.getElementById('discovery-count');
    if (!list) return;
    if (!_discoveryProbeWired) wireDiscoveryProbe();

    const subs = discovery.filter(d => d.subscribed).length;
    if (count) count.textContent = discovery.length
        ? `${subs}/${discovery.length} sources subscribed` : '';

    // Sources with a band row above already show their controls there; only
    // the rest (unsubscribed, or subscribed-but-silent) render here.
    const bandIds = new Set(
        [...document.querySelectorAll('#heatmap-grids .band-row')].map(r => r.dataset.id));
    const avail = discovery.filter(d => !bandIds.has(d.device_id));

    // Rebuild the list ONLY when its meaningful state changes, not every WS tick.
    // Rebuilding ~5x/sec destroyed the role <select> mid-interaction, so it
    // "glitched" and could not be used (Tory). `age` is excluded from the
    // signature (it ticks every frame) and refreshed in place instead, so an open
    // dropdown / focused control is never blown away.
    const sig = JSON.stringify(avail.map(d => [
        d.device_id, d.role || '', !!d.subscribed, d.sub_error || '',
        d.device_type, d.ip, d.cmd_port, d.source,
    ]));
    if (sig === _discoverySig) {
        const byId = {};
        avail.forEach(d => { byId[d.device_id] = d; });
        list.querySelectorAll('li.src').forEach(li => {
            const d = byId[li.dataset.id];
            const ageEl = li.querySelector('.age');
            if (d && ageEl) ageEl.textContent = (d.age_s != null) ? `${d.age_s.toFixed(0)}s ago` : '';
        });
        return;
    }
    _discoverySig = sig;

    if (!avail.length) {
        // Everything discovered graduated to a band row (or nothing is
        // discovered at all); an empty list collapses via CSS :empty.
        list.innerHTML = discovery.length
            ? '' : '<li class="empty">No V4 sources discovered yet…</li>';
        return;
    }
    list.innerHTML = avail.map(d => {
        // State badge: subscribed (green, awaiting frames) / error (red) /
        // known (grey).
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
                ${roleSelectHtml(d)}
                <button class="src-btn" data-act="${btnAct}" data-id="${escapeHtml(d.device_id)}">${btnTxt}</button>
            </li>`;
    }).join('');

    wireRoleSelects(list);
    wireSubButtons(list);
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

    const scanBtn = document.getElementById('discovery-scan-btn');
    if (scanBtn) {
        scanBtn.onclick = async () => {
            scanBtn.disabled = true;
            setProbeMsg('scanning subnet…', false);
            try {
                const res = await fetch('/api/discovery/scan', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({}),
                });
                if (res.ok) {
                    const r = await res.json();
                    const n = (r.found || []).length;
                    setProbeMsg(n ? `scan found ${n}: ${r.found.join(', ')}`
                                  : 'scan found no new sources', false);
                } else {
                    const err = await res.json().catch(() => ({}));
                    setProbeMsg(err.detail || 'scan failed', true);
                }
            } catch (err) {
                setProbeMsg(String(err), true);
            } finally {
                scanBtn.disabled = false;
            }
        };
    }
}

function setProbeMsg(text, isError) {
    const el = document.getElementById('discovery-probe-msg');
    if (!el) return;
    el.textContent = text;
    el.classList.toggle('err', !!isError);
}

// "+ add source" collapsible: the probe-by-IP form + subnet scan moved out
// of the old Devices rail, collapsed by default (same caret pattern as the
// record-advanced toggle).
{
    const toggle = document.getElementById('add-source-toggle');
    const body = document.getElementById('add-source-body');
    if (toggle && body) {
        toggle.onclick = () => {
            const hidden = body.classList.toggle('hidden');
            toggle.textContent = (hidden ? '▸' : '▾') + ' add source';
        };
    }
}

// ---------- device selection + status helpers ----------
// (renderDevices and its #device-list are gone; band cards next to each
// heatmap + label status lines under the comparator visuals replaced them.)

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

// Battery + uptime + rssi line, now rendered inside each band card's
// .band-stats (renderBandCards). Returns '' when the device never reported
// a meta field (legacy firmware).
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

    // No raw IMU counts here anymore: the Orientation widget under the band
    // card shows the fused pose, and the debug panel keeps the raw numbers.
    // The counts blew out the narrow card and rewrote its DOM ~20x/sec.

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

// Render one BAND ROW per streaming flexgrid, in a STABLE order (left, then
// right, then others) so the two hands never swap positions. Each row is
// [band card | its heatmap] (Tory's redesign: the flexgrid square sits to
// the left of its heatmap, in line); renderBandCards fills the card, this
// draws the canvas. The per-device canvases are REUSED across ticks exactly
// as before; only the wrapping row scaffold changed.
function drawHeatmaps() {
    const grids = document.getElementById('heatmap-grids');
    if (!grids) return;
    const roleOrder = r => (r === 'left' ? 0 : r === 'right' ? 1 : 2);
    const flex = lastDevices
        .filter(d => d.device_type === 'flexgrid' && Array.isArray(d.matrix) && d.matrix.length)
        .sort((a, b) => roleOrder(a.role) - roleOrder(b.role)
                        || String(a.device_id).localeCompare(String(b.device_id)));
    if (!flex.length) {
        if (grids._ids !== '') {
            parkImuViewer();   // innerHTML wipe would destroy the IMU canvas
            grids.innerHTML = '<div class="heatmap-empty">Waiting for a flexgrid…</div>';
            grids._ids = '';
            _bandCardsSig = null;
        }
        return;
    }
    // Shared vmax across ALL bands so the two hands compare on one scale (sticky
    // high-water-mark + 1.2x headroom; never auto-shrinks).
    let observedMax = 0;
    for (const d of flex) for (const col of d.matrix) for (const v of col) if (v > observedMax) observedMax = v;
    if (observedMax > heatmapVmax) heatmapVmax = Math.min(4096, Math.floor(observedMax * 1.2));
    // Rebuild the row scaffold only when the set of bands changes (avoids
    // canvas thrash + flicker every tick). The .imu-slot under the card is
    // where renderImuViewer re-homes the single Orientation widget.
    const ids = flex.map(d => d.device_id).join(',');
    if (grids._ids !== ids) {
        parkImuViewer();   // moving beats rebuilding: keeps the IMU canvas alive
        grids.innerHTML = flex.map(d => `
            <div class="band-row" data-id="${escapeHtml(d.device_id)}">
                <div class="band-side">
                    <div class="band-card"></div>
                    <div class="imu-slot"></div>
                </div>
                <div class="heatmap-cell">
                    <div class="heatmap-cell-label"></div>
                    <canvas></canvas>
                </div>
            </div>`).join('');
        grids._ids = ids;
        _bandCardsSig = null;   // fresh scaffold: cards must re-render
    }
    flex.forEach((d, i) => {
        const row = grids.children[i];
        // Scope to .heatmap-cell: the band card's .imu-slot can hold the
        // Orientation widget's WebGL canvas, and a bare querySelector('canvas')
        // would grab THAT one (getContext('2d') on it returns null).
        if (row) drawHeatmapInto(d, row.querySelector('.heatmap-cell canvas'),
                                 row.querySelector('.heatmap-cell-label'));
    });
}

// Park the single IMU widget in its dock before an innerHTML wipe of the
// band rows; renderImuViewer re-homes it under the right card next tick.
// (appendChild MOVES the node, so the WebGL context survives.)
function parkImuViewer() {
    const dock = document.getElementById('imu-dock');
    const viewer = document.getElementById('imu-viewer');
    if (dock && viewer && viewer.parentElement !== dock) dock.appendChild(viewer);
}

function drawHeatmapInto(dev, canvasEl, labelEl) {
    if (!canvasEl) return;
    const cx = canvasEl.getContext('2d');
    const matrix = dev.matrix;  // [cols][rows]
    if (!matrix || !matrix.length) return;
    const cols = matrix.length;
    const rows = matrix[0].length;

    let observedMax = 0;
    for (let c = 0; c < cols; c++) {
        for (let r = 0; r < rows; r++) {
            if (matrix[c][r] > observedMax) observedMax = matrix[c][r];
        }
    }
    if (labelEl) {
        const roleTag = dev.role
            ? `<span class="hm-role hm-${escapeHtml(dev.role)}">${escapeHtml(dev.role)}</span> ` : '';
        labelEl.innerHTML = `${roleTag}<b>${escapeHtml(dev.device_id)}</b> · ${rows}×${cols}`
            + ` · ${dev.hz.toFixed(1)} Hz · max ${observedMax}`;
    }

    // Resize canvas to fit the matrix aspect ratio nicely
    const w = canvasEl.clientWidth || 400;
    const h = Math.max(140, Math.floor(w * (rows / cols) * 1.3));
    if (canvasEl.width !== w || canvasEl.height !== h) {
        canvasEl.width = w;
        canvasEl.height = h;
    }

    const cellW = w / cols;
    const cellH = h / rows;

    // Solid background — cells fully overdraw it.
    cx.fillStyle = '#1a1f2b';
    cx.fillRect(0, 0, w, h);

    for (let c = 0; c < cols; c++) {
        for (let r = 0; r < rows; r++) {
            const v = matrix[c][r];
            cx.fillStyle = pressureColor(v, heatmapVmax);
            cx.fillRect(c * cellW, r * cellH, cellW - 1, cellH - 1);
            // Show numeric value once it's above the noise gate — useful for
            // seeing exactly how much "bleed" a neighbor cell has.
            if (v >= 50) {
                const t = v / heatmapVmax;
                cx.fillStyle = t > 0.55 ? '#0b0d12' : '#e7e9ee';
                cx.font = `${Math.floor(Math.min(cellW, cellH) * 0.30)}px ui-monospace, monospace`;
                cx.textBaseline = 'middle';
                cx.textAlign = 'center';
                cx.fillText(v, c * cellW + cellW / 2, r * cellH + cellH / 2);
            }
        }
    }
}

// Band heatmap layout: side by side (default) vs stacked one above the other.
// Persisted so it survives reloads. The .stacked class lives on #heatmap-grids
// itself, so drawHeatmaps() rebuilding the inner cells never clears it.
const HEATMAP_LAYOUT_KEY = 'om.heatmap_layout';   // '' = side by side | 'stacked'
function applyHeatmapLayout() {
    const grids = document.getElementById('heatmap-grids');
    const btn = document.getElementById('heatmap-layout-toggle');
    const stacked = localStorage.getItem(HEATMAP_LAYOUT_KEY) === 'stacked';
    if (grids) grids.classList.toggle('stacked', stacked);
    if (btn) btn.textContent = stacked ? '▥ Side by side' : '▤ Stack';
}
{
    const btn = document.getElementById('heatmap-layout-toggle');
    if (btn) btn.onclick = () => {
        const stacked = localStorage.getItem(HEATMAP_LAYOUT_KEY) === 'stacked';
        localStorage.setItem(HEATMAP_LAYOUT_KEY, stacked ? '' : 'stacked');
        applyHeatmapLayout();
    };
    applyHeatmapLayout();
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

let pastSessions = [];

// Compact session chip in the control bar: session context stays visible next
// to Record even when the full session card (Data stage) is scrolled away.
function updateSessionChip() {
    if (!sessionChip) return;
    if (activeSession) {
        const s = activeSession;
        const dur = s.started_at ? Math.floor(Date.now() / 1000 - s.started_at) : 0;
        sessionChip.className = 'session-chip on';
        sessionChip.textContent = `● ${s.name || s.id} | ${s.capture_count || 0} cap | ${formatUptime(dur)}`;
        sessionChip.title = 'Active session · click to jump to the Data stage';
    } else {
        sessionChip.className = 'session-chip off';
        sessionChip.textContent = 'no session (captures ungrouped)';
        sessionChip.title = 'Captures will not be grouped · start a session';
    }
    if (sessionChipNew) sessionChipNew.style.display = activeSession ? 'none' : '';
}
if (sessionChip) sessionChip.onclick = () => {
    const el = document.getElementById('stage-data');
    if (el) el.scrollIntoView({ behavior: 'smooth' });
};
if (sessionChipNew) sessionChipNew.onclick = () => openSessionModal();

// Skip rebuilding the session card every WS tick: rebuilding ~5x/sec recreated
// the End/Add buttons mid-click, so "End session" often didn't register (the
// click landed between two rebuilds). Rebuild ONLY when meaningful state
// changes; the live elapsed timer is refreshed in place. 'NONE' marks the
// no-session card so it renders exactly once on entry.
let _activeSessionSig = null;

function renderActiveSession() {
    // Control-bar chip + captures-filter session plumbing ride the same
    // per-tick call (both are cheap and sig/id-gated internally).
    updateSessionChip();
    syncCaptureFilterSession();
    renderCaptureSessionOptions();
    if (activeSession) {
        const s = activeSession;
        const dur = s.started_at ? Math.floor(Date.now()/1000 - s.started_at) : 0;
        const nDev = (s.context && Array.isArray(s.context.devices)) ? s.context.devices.length : 0;
        const sig = JSON.stringify([s.id, s.name, s.arm, s.wearer, s.subject, s.take,
            s.labeler_source, s.capture_count, s.notes, nDev, (s.gestures || []).join(',')]);
        if (sig !== _activeSessionSig) {
            _activeSessionSig = sig;
            const armCls = s.arm === 'left' ? 'arm-left' : (s.arm === 'right' ? 'arm-right' : '');
            const armBit = s.arm ? `<span class="${armCls}">${escapeHtml(s.arm)} arm</span>` : '<span class="empty">no arm set</span>';
            const who = s.wearer || s.subject;
            const whoBit = who ? ' · ' + escapeHtml(who) : '';
            const takeBit = (typeof s.take === 'number') ? ` · take ${s.take}` : '';
            const labelerBit = s.labeler_source ? ` · ${escapeHtml(s.labeler_source)}` : '';
            const devBit = nDev ? ` · ${nDev} dev @ start` : '';
            const gestures = (s.gestures || []).length
                ? ' · planned: ' + escapeHtml((s.gestures || []).join(', '))
                : '';
            activeSessionArea.innerHTML = `
                <div class="session-card active">
                    <div class="session-head">
                        <div>
                            <span class="session-id">${escapeHtml(s.name || s.id)}</span>
                            <span class="session-meta-line">${armBit}${whoBit}${takeBit}${labelerBit} · ${s.capture_count || 0} captures · <span id="session-dur">${formatUptime(dur)}</span>${devBit}${gestures}</span>
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
        } else {
            // Structure unchanged: refresh only the live timer, leave the buttons.
            const d = document.getElementById('session-dur');
            if (d) d.textContent = formatUptime(dur);
        }
    } else if (_activeSessionSig !== 'NONE') {
        _activeSessionSig = 'NONE';
        activeSessionArea.innerHTML = '<div class="session-empty">No active session — recordings won\'t be grouped. Click "New session" to start one.</div>';
        sessionStartBtn.disabled = false;
        sessionStartBtn.title = '';
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

async function openSessionModal() {
    sessionModal.classList.add('open');
    sessionModal.setAttribute('aria-hidden', 'false');
    // Prefill wearer / arm / labeler from the most recent session and bump the
    // take number, so a repeat session is one edit (Tory can't type well in-VR,
    // and this halves the friction on the PC too). Only fills empty fields.
    try {
        const r = await fetch('/api/sessions');
        if (r.ok) {
            const last = (await r.json())[0];
            if (last) {
                const w = document.getElementById('sess-wearer');
                if (w && !w.value) w.value = last.wearer || last.subject || '';
                const a = document.getElementById('sess-arm');
                if (a && !a.value && last.arm) a.value = last.arm;
                const l = document.getElementById('sess-labeler');
                if (l && !l.value && last.labeler_source) l.value = last.labeler_source;
                const t = document.getElementById('sess-take');
                if (t && !t.value && typeof last.take === 'number') t.value = last.take + 1;
            }
        }
    } catch (e) { /* prefill is best-effort */ }
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
    const takeRaw = parseInt(document.getElementById('sess-take').value, 10);
    const body = {
        name:     document.getElementById('sess-name').value.trim(),
        subject:  document.getElementById('sess-subject').value.trim(),
        arm:      document.getElementById('sess-arm').value || null,
        gestures: document.getElementById('sess-gestures').value.split(',').map(s=>s.trim()).filter(Boolean),
        tags:     document.getElementById('sess-tags').value.split(',').map(s=>s.trim()).filter(Boolean),
        notes:    document.getElementById('sess-notes').value,
        wearer:   document.getElementById('sess-wearer').value.trim(),
        take:     Number.isFinite(takeRaw) ? takeRaw : null,
        labeler_source: document.getElementById('sess-labeler').value || '',
        video_ref: document.getElementById('sess-video').value.trim(),
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
        recordBtn.textContent = '■ Stop';
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

        // At-a-glance capture-quality verdict so a bad take is caught in real time
        // (data-capture quality is goal #1, board #0297) instead of after the fact.
        // Joints dropping mid-capture pad/truncate the label row -> a quality hit.
        const widthMiss = r.label_width_mismatch ?? 0;
        const seen = r.sensor_frames_seen ?? 0;
        // band_flat (server-side): most cells never moved = band not engaged.
        // The capture_1779731309 dud class -- looks like a normal file, trains
        // nothing (board #0311.4).
        const bandFlat = r.band_flat === true;
        let verdict = 'GOOD', vCls = 'cap-good';
        if (bandFlat || rate < 0.5 || (seen > 20 && (r.matched ?? 0) === 0)) {
            verdict = 'BAD'; vCls = 'cap-bad';
        } else if (rate < 0.9 || widthMiss > 0) {
            verdict = 'DEGRADED'; vCls = 'cap-warn';
        }
        const widthLine = widthMiss > 0
            ? `<div class="cap-warn-line">⚠ ${widthMiss} frame(s) had joints drop mid-capture (label width padded/truncated)</div>`
            : '';
        const flatLine = bandFlat
            ? `<div class="cap-warn-line">⚠ band not engaged: ${r.flat_cells}/${r.cells_total} cells flat — is the band on the arm?</div>`
            : '';

        recordStatus.innerHTML = `
            <div class="cap-verdict ${vCls}">capture: ${verdict}</div>
            <div>${escapeHtml(r.filename)} · ${r.rows} paired rows · ${r.duration_s}s · win ${r.window_ms ?? 100}ms</div>
            <div>${sensorLine} &nbsp; label: <b>${escapeHtml(label)}</b></div>
            <div>matched: ${r.matched ?? 0} / ${seen}
                 (<span class="${rateCls}">${ratePct}%</span>)
                 · unpaired sensor: ${r.unpaired_sensor ?? 0}
                 · label pkts: ${r.label_packets_seen ?? 0}</div>
            ${flatLine}${widthLine}
        `;
    } else {
        recordBtn.textContent = '● Record';
        recordBtn.classList.remove('recording');
        if (recordMultibandBtn) recordMultibandBtn.disabled = false;
        // Keep the post-stop result card (verdict + next-take actions) up
        // until the operator acts on it; the next start clears the flag.
        if (!_postStopCard) recordStatus.textContent = '';
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

// ---------- record plan: one Record button, derived mode ----------
//
// The three record buttons (single / multi-band / two-hand) collapsed into ONE
// Record button (Tory's UI-overhaul ask). The mode is derived per tick from
// the Labels pick + which devices are streaming; #record-plan previews the
// derived call so the operator sees exactly what one click will do. Adding a
// future label device (keyboard/controller) = one <option> + one branch here.

const DEVICE_FRESH_S = 2.0;   // same staleness threshold as the device list

function deviceFresh(d) {
    return d.last_seen_age == null || d.last_seen_age < DEVICE_FRESH_S;
}

function deriveRecordPlan() {
    const winRaw = recordWindowInput ? parseInt(recordWindowInput.value, 10) : NaN;
    const winMs = Number.isFinite(winRaw) ? winRaw : null;
    // Server defaults when the advanced window box is empty: lask5=100,
    // quest_hand=175 (DEFAULT_WINDOW_MS_BY_TYPE in state.py).
    const winText = def => `win ${winMs ?? def}ms`;
    const blocked = reason => ({ ready: false, mode: 'blocked', reason });

    // Advanced override: an explicit device pick forces the legacy
    // single-source path (today's exact behavior).
    const sensorVal = sensorSelect.value;
    const labelVal  = labelSelect.value;
    if (sensorVal || labelVal) {
        const body = { filename: null };
        if (sensorVal) body.sensor_device_id = sensorVal;
        if (labelVal === '__none__') body.label_device_id = '';
        else if (labelVal)           body.label_device_id = labelVal;
        return {
            ready: true, mode: 'advanced', endpoint: '/api/recording', body,
            summary: `advanced | sensor ${sensorVal || 'auto'} | label ${
                labelVal === '__none__' ? 'none' : (labelVal || 'auto')} | ${winText(100)}`,
        };
    }

    const flex   = lastDevices.filter(d => d.device_type === 'flexgrid');
    const bandL  = flex.find(d => d.role === 'left');
    const bandR  = flex.find(d => d.role === 'right');
    const tagged = flex.filter(d => d.role === 'left' || d.role === 'right');
    // Single-band candidate: the one tagged band, or the only flexgrid seen.
    const soloBand = tagged.length === 1 ? tagged[0]
        : (tagged.length === 0 && flex.length === 1 ? flex[0] : null);
    const quests = lastDevices.filter(d => d.device_type === 'quest_hand');
    // Same side test as renderHandViewer (quest-left / quest-right ids).
    const questSide = side => quests.find(d =>
        String(d.device_id).toLowerCase().includes(side));
    const lask = lastDevices.find(d => d.device_type === 'lask5' && deviceFresh(d));
    const source = (labelSourceSelect && labelSourceSelect.value) || 'vr';

    if (!flex.length) return blocked('no band streaming');

    if (source === 'vr') {
        if (bandL && bandR) {
            const missing = ['left', 'right'].filter(s => {
                const q = questSide(s);
                return !(q && deviceFresh(q));
            });
            if (missing.length) return blocked(
                `2 bands tagged, but VR ${missing.join('+')} hand not streaming: open /vr with ?arm=both`);
            return {
                ready: true, mode: '2h-vr',
                endpoint: '/api/recording/bilateral',
                body: { filename: null },
                summary: `2H | L ${bandL.device_id} + R ${bandR.device_id} | labels: VR both hands | ${winText(175)}`,
            };
        }
        if (!soloBand) return blocked('no band tagged left/right: tag roles in Sources');
        if (!quests.some(deviceFresh)) return blocked('VR hand not streaming: open /vr on the Quest');
        const role = soloBand.role || 'left';
        return {
            ready: true, mode: '1h-vr',
            endpoint: '/api/recording',
            // label_device_id omitted: server auto-pick prefers quest_hand
            body: { filename: null, sensor_device_id: soloBand.device_id, role },
            summary: `1H | ${soloBand.device_id} (${role}) | labels: VR ${role} hand | ${winText(175)}`,
        };
    }

    if (source === 'lask5') {
        if (!lask) return blocked('no LASK5 streaming');
        if (bandL && bandR) {
            return {
                ready: true, mode: '2band-lask',
                endpoint: '/api/recording/multiband',
                body: { filename: null },
                summary: `2 bands | labels: LASK5 ${lask.device_id} | ${winText(100)}`,
            };
        }
        if (!soloBand) return blocked('no band tagged left/right: tag roles in Sources');
        // Omit both device ids for a lone untagged band = server auto-pick.
        const body = { filename: null };
        if (soloBand.role) {
            body.sensor_device_id = soloBand.device_id;
            body.role = soloBand.role;
        }
        return {
            ready: true, mode: '1h-lask',
            endpoint: '/api/recording', body,
            summary: `1H | ${soloBand.device_id}${soloBand.role ? ` (${soloBand.role})` : ''} | labels: LASK5 ${lask.device_id} | ${winText(100)}`,
        };
    }

    if (source === 'gamepad') {
        // The dashboard browser streams the controller to /ws/gamepad, so it
        // shows up here as a device_type="gamepad" label device (gamepad.js).
        const pad = lastDevices.find(d => d.device_type === 'gamepad' && deviceFresh(d));
        if (!pad) return blocked('no controller: plug in a USB gamepad and press a button');
        if (bandL && bandR) {
            // multiband auto-picks the label device; gamepad is in the server's
            // AUTO_LABEL_TYPE_PREFERENCE so a lone controller is found.
            return {
                ready: true, mode: '2band-gamepad',
                endpoint: '/api/recording/multiband',
                body: { filename: null },
                summary: `2 bands | labels: gamepad ${pad.device_id} | ${winText(120)}`,
            };
        }
        if (!soloBand) return blocked('no band tagged left/right: tag roles in Sources');
        // Pin the gamepad as the label so auto-pick can't prefer a stray quest/lask.
        const body = { filename: null, label_device_id: pad.device_id };
        if (soloBand.role) { body.sensor_device_id = soloBand.device_id; body.role = soloBand.role; }
        return {
            ready: true, mode: '1h-gamepad',
            endpoint: '/api/recording', body,
            summary: `1H | ${soloBand.device_id}${soloBand.role ? ` (${soloBand.role})` : ''} | labels: gamepad ${pad.device_id} | ${winText(120)}`,
        };
    }

    // source === 'none': sensor-only (label_device_id "" disables pairing)
    if (bandL && bandR) {
        if (!(deviceFresh(bandL) && deviceFresh(bandR))) {
            return blocked('band(s) stale: check both bands are streaming');
        }
        return {
            ready: true, mode: 'sensor-2band',
            endpoint: '/api/recording',
            body: { filename: null, label_device_id: '', sensor_device_id: bandL.device_id,
                    role: 'left', extra_sensors: [{ device_id: bandR.device_id, role: 'right' }] },
            summary: `2 bands | L ${bandL.device_id} + R ${bandR.device_id} | labels: none (sensor only) | ${winText(100)}`,
        };
    }
    if (!soloBand) return blocked('no band tagged left/right: tag roles in Sources');
    if (!deviceFresh(soloBand)) return blocked('band stale: is it streaming?');
    const role = soloBand.role || 'left';
    return {
        ready: true, mode: 'sensor-only',
        endpoint: '/api/recording',
        body: { filename: null, label_device_id: '', sensor_device_id: soloBand.device_id, role },
        summary: `1 band | ${soloBand.device_id} (${role}) | labels: none (sensor only) | ${winText(100)}`,
    };
}

// Plan preview line + Record-button arming. Runs every WS tick but touches
// the DOM only when the rendered content changes (open dropdowns must survive
// ticks -- same rule as _discoverySig).
let _recordPlanSig = null;

function renderRecordPlan() {
    const recording = !!recordingState;
    const plan = recording ? null : deriveRecordPlan();
    if (controlBar) controlBar.classList.toggle('recording', recording);
    if (labelSourceSelect) labelSourceSelect.disabled = recording;
    if (recordWindowInput) recordWindowInput.disabled = recording;
    recordBtn.disabled = !recording && !(plan && plan.ready);
    const sig = JSON.stringify(plan
        ? [plan.mode, plan.ready, plan.summary, plan.reason, recording]
        : ['recording', recording]);
    if (sig === _recordPlanSig) return;
    _recordPlanSig = sig;
    if (!recordPlanEl) return;
    if (!plan) { recordPlanEl.textContent = ''; return; }
    recordPlanEl.classList.toggle('blocked', !plan.ready);
    recordPlanEl.textContent = plan.ready ? plan.summary : (plan.reason || '');
}

// Labels pick persists across reloads; default vr (the headset flow).
if (labelSourceSelect) {
    const saved = localStorage.getItem(STORE_LABEL_SOURCE);
    if (saved && ['vr', 'lask5', 'none', 'gamepad'].includes(saved)) labelSourceSelect.value = saved;
    labelSourceSelect.addEventListener('change', () => {
        localStorage.setItem(STORE_LABEL_SOURCE, labelSourceSelect.value);
    });
}

if (recordAdvancedToggle && recordAdvancedEl) {
    recordAdvancedToggle.onclick = () => {
        const hidden = recordAdvancedEl.classList.toggle('hidden');
        recordAdvancedToggle.textContent = (hidden ? '▸' : '▾') + ' advanced';
    };
}

// Post-stop result card: keeps the finished take's stats + next actions in
// the sticky bar until the operator moves on. renderRecording leaves the
// status line alone while this flag is set; the next start clears it.
let _postStopCard = false;

function showPostStopCard(result, lastRec) {
    // Same verdict thresholds as the live block in renderRecording, fed from
    // the stop result (band_flat only lives in the last live tick snapshot).
    const rate = result.match_rate ?? 0;
    const widthMiss = result.label_width_mismatch ?? 0;
    const seen = result.sensor_frames_seen ?? 0;
    const bandFlat = !!(lastRec && lastRec.band_flat);
    let verdict = 'GOOD', vCls = 'cap-good';
    if (bandFlat || rate < 0.5 || (seen > 20 && (result.matched ?? 0) === 0)) {
        verdict = 'BAD'; vCls = 'cap-bad';
    } else if (rate < 0.9 || widthMiss > 0) {
        verdict = 'DEGRADED'; vCls = 'cap-warn';
    }
    const flatLine = bandFlat
        ? `<div class="cap-warn-line">⚠ band not engaged: ${lastRec.flat_cells}/${lastRec.cells_total} cells flat (was the band on the arm?)</div>`
        : '';
    const sessionBit = activeSession
        ? ` · linked to ${escapeHtml(activeSession.name || activeSession.id)}`
        : ' · no session (ungrouped)';
    recordStatus.innerHTML = `
        <div class="cap-verdict ${vCls}">saved: ${verdict}</div>
        <div>${escapeHtml(result.filename || '?')} · ${result.rows ?? 0} rows · ${result.duration_s ?? 0}s
             · match ${(rate * 100).toFixed(1)}%${sessionBit}</div>
        ${flatLine}
        <div class="rec-next-actions">
            <button class="link" id="rec-next-take">● Record next take</button>
            <button class="link" id="rec-go-train">⚙ Go train</button>
        </div>`;
    _postStopCard = true;
    const nextBtn = document.getElementById('rec-next-take');
    if (nextBtn) nextBtn.onclick = () => {
        // Re-arm with a suggested take name; the next click on Record starts.
        const base = activeSession ? (activeSession.name || activeSession.id) : 'take';
        const n = (activeSession ? (activeSession.capture_count || 0) : 0) + 1;
        captureName.value = `${base}-take${n}`;
        _postStopCard = false;
        recordStatus.textContent = '';
        captureName.focus();
    };
    const goTrain = document.getElementById('rec-go-train');
    if (goTrain) goTrain.onclick = () => {
        // Pre-check the fresh capture so Train is one click away.
        if (result.filename) {
            selectedCaptures.add(result.filename);
            updateSelectionStatus();
        }
        const el = document.getElementById('stage-data');
        if (el) el.scrollIntoView({ behavior: 'smooth' });
    };
}

recordBtn.onclick = async () => {
    try {
        if (recordingState) {
            const lastRec = recordingState;   // last live snapshot (carries band_flat)
            const r = await fetch('/api/recording', { method: 'DELETE' });
            if (!r.ok) throw new Error(await readError(r));
            const result = await r.json().catch(() => null);
            if (result) showPostStopCard(result, lastRec);
            await refreshCaptures();
        } else {
            const plan = deriveRecordPlan();
            if (!plan.ready) return;
            const body = Object.assign({}, plan.body);
            body.filename = captureName.value.trim() || null;
            const win = recordWindowInput ? parseInt(recordWindowInput.value, 10) : NaN;
            if (Number.isFinite(win)) body.window_ms = win;
            const r = await fetch(plan.endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!r.ok) throw new Error(await readError(r));
            captureName.value = '';
            _postStopCard = false;   // live verdict block owns the status line now
        }
    } catch (e) {
        alert(`Error: ${e.message}`);
    }
};

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

// ---------- captures filter bar ----------
//
// Session / date / label-source / hands filters compose with AND; within a
// chip group an empty selection passes all, multiple picks OR together. Junk
// takes (too short to train on) hide by default. Replaces the old binary
// "active session vs show all" view (Tory's UI-overhaul ask: filters).

const capFilter = {
    session: '__all__',       // '__active__' | '__all__' | '__none__' | <session_id>
    days: null,               // null | 1 | 7
    labels: new Set(),        // subset of {'quest','lask5'}
    hands: new Set(),         // subset of {1,2}
    showJunk: false,
};

const capFilterSession = document.getElementById('cap-filter-session');
const capFilterJunk    = document.getElementById('cap-filter-junk');
const capJunkCount     = document.getElementById('cap-junk-count');

// Recording-time epoch ms for date logic + display. Falls back to file mtime
// for legacy captures; created_ms is preferred because this checkout syncs
// via OneDrive, which can rewrite mtimes.
function captureTimeMs(c) {
    return (c.created_ms != null) ? c.created_ms : c.mtime * 1000;
}

// Junk = not enough paired rows / seconds to train on. rows/duration_s are
// the new backend fields; size is the fallback for pre-field captures.
function captureIsJunk(c) {
    if (c.rows != null && c.rows < 200) return true;
    if (c.duration_s != null && c.duration_s < 10) return true;
    if (c.rows == null && c.size_bytes < 51200) return true;
    return false;
}

function applyCaptureFilters(list) {
    return list.filter(c => {
        if (capFilter.session === '__active__') {
            if (!activeSession || c.session_id !== activeSession.id) return false;
        } else if (capFilter.session === '__none__') {
            if (c.session_id) return false;
        } else if (capFilter.session !== '__all__') {
            if (c.session_id !== capFilter.session) return false;
        }
        if (capFilter.days != null
            && captureTimeMs(c) < Date.now() - capFilter.days * 86400e3) return false;
        // null label_source passes only when no label chip is on
        if (capFilter.labels.size && !capFilter.labels.has(c.label_source)) return false;
        if (capFilter.hands.size && !capFilter.hands.has(c.hands)) return false;
        return true;
    });
}

// Session select: Active (when one exists) / All / unlinked / each past
// session. Rebuilt only when the id list changes so an open dropdown
// survives the 5Hz ticks driving this via renderActiveSession.
let _capSessOptsSig = null;

function renderCaptureSessionOptions() {
    if (!capFilterSession) return;
    const sig = JSON.stringify([activeSession ? activeSession.id : null,
                                pastSessions.map(s => s.id)]);
    if (sig !== _capSessOptsSig) {
        _capSessOptsSig = sig;
        const opts = [];
        if (activeSession) opts.push(['__active__', 'Active session']);
        opts.push(['__all__', 'All sessions']);
        opts.push(['__none__', 'No session (unlinked)']);
        for (const s of pastSessions) {
            const d = s.started_at ? new Date(s.started_at * 1000) : null;
            const mmdd = d ? ` (${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')})` : '';
            opts.push([s.id, `${s.name || s.id}${mmdd}`]);
        }
        capFilterSession.innerHTML = opts.map(([v, label]) =>
            `<option value="${escapeHtml(v)}">${escapeHtml(label)}</option>`).join('');
    }
    // A removed option (e.g. session deleted) falls back to All.
    if (![...capFilterSession.options].some(o => o.value === capFilter.session)) {
        capFilter.session = '__all__';
    }
    if (capFilterSession.value !== capFilter.session) {
        capFilterSession.value = capFilter.session;
    }
}

// Snap the session filter on session start/end (driven from the WS tick via
// renderActiveSession): start -> Active session, end -> All sessions.
let _capFilterSessionId;   // undefined = before first tick

function syncCaptureFilterSession() {
    const sid = activeSession ? activeSession.id : null;
    if (sid === _capFilterSessionId) return;
    _capFilterSessionId = sid;
    capFilter.session = sid ? '__active__' : '__all__';
    // Immediate re-render with whatever list we have; handleTick's
    // refreshCaptures (on session change) replaces it once fetched.
    renderCaptures(_lastCapturesList);
}

if (capFilterSession) capFilterSession.onchange = () => {
    capFilter.session = capFilterSession.value;
    renderCaptures(_lastCapturesList);
};
// Date chips are exclusive (one on at a time)
document.querySelectorAll('#cap-filter-date .filter-chip').forEach(btn => {
    btn.onclick = () => {
        capFilter.days = btn.dataset.days ? parseInt(btn.dataset.days, 10) : null;
        document.querySelectorAll('#cap-filter-date .filter-chip').forEach(b =>
            b.classList.toggle('on', b === btn));
        renderCaptures(_lastCapturesList);
    };
});
// Label + hands chips are multi-select toggles
document.querySelectorAll('#cap-filter-label .filter-chip').forEach(btn => {
    btn.onclick = () => {
        const v = btn.dataset.label;
        if (capFilter.labels.has(v)) capFilter.labels.delete(v);
        else capFilter.labels.add(v);
        btn.classList.toggle('on', capFilter.labels.has(v));
        renderCaptures(_lastCapturesList);
    };
});
document.querySelectorAll('#cap-filter-hands .filter-chip').forEach(btn => {
    btn.onclick = () => {
        const v = parseInt(btn.dataset.hands, 10);
        if (capFilter.hands.has(v)) capFilter.hands.delete(v);
        else capFilter.hands.add(v);
        btn.classList.toggle('on', capFilter.hands.has(v));
        renderCaptures(_lastCapturesList);
    };
});
if (capFilterJunk) capFilterJunk.onchange = () => {
    capFilter.showJunk = capFilterJunk.checked;
    renderCaptures(_lastCapturesList);
};

let _lastCapturesList = [];

// Rebuild gate (same convention as _modelsSig): the 5s poll must not rebuild
// the tbody under the cursor when nothing it renders has changed.
let _capturesSig = null;

function renderCaptures(list) {
    _lastCapturesList = list;
    // Prune selection set down to captures that still exist
    const existing = new Set(list.map(c => c.name));
    for (const n of [...selectedCaptures]) {
        if (!existing.has(n)) selectedCaptures.delete(n);
    }

    const filtered = applyCaptureFilters(list);
    const junkCount = filtered.filter(captureIsJunk).length;
    const visible = capFilter.showJunk ? filtered : filtered.filter(c => !captureIsJunk(c));
    if (capJunkCount) capJunkCount.textContent =
        (!capFilter.showJunk && junkCount) ? `(${junkCount} junk hidden)` : '';

    const sig = JSON.stringify([
        capFilter.session, capFilter.days, [...capFilter.labels],
        [...capFilter.hands], capFilter.showJunk,
        activeSession ? activeSession.id : null,
        // meta rides along so an edit re-renders even though mtime is unchanged
        visible.map(c => [c.name, c.mtime, c.session_id || '', c.hands,
                          c.label_source || '', c.rows, c.duration_s, c.meta]),
    ]);
    if (sig === _capturesSig) { updateSelectionStatus(); return; }
    _capturesSig = sig;

    if (!list.length) {
        capturesBody.innerHTML = '<tr class="empty"><td colspan="6">No captures saved yet.</td></tr>';
        updateSelectionStatus();
        return;
    }

    const rows = visible.map(c => {
        const date = new Date(captureTimeMs(c)).toLocaleString();
        const kb = (c.size_bytes / 1024).toFixed(1);
        const checked = selectedCaptures.has(c.name) ? 'checked' : '';
        const metaCell = renderCaptureMetaSummary(c.meta);
        const junk = captureIsJunk(c);
        const trClass = junk ? ' class="junk-row"' : '';
        const junkTag = junk
            ? ' <span class="tag" title="too few rows / too short to train on">junk</span>' : '';
        // 1H/2H badge: was this take one band or a true two-hand capture?
        // (backend list_captures counts left+right roles in the CSV.)
        const hands = c.hands === 2
            ? '<span class="hands-badge h2" title="two-hand capture (left + right bands)">2H</span>'
            : (c.hands === 1
                ? '<span class="hands-badge h1" title="single-band capture">1H</span>' : '');
        return `<tr${trClass} data-name="${escapeHtml(c.name)}">
            <td class="captures-check"><input type="checkbox" class="cap-check" data-name="${escapeHtml(c.name)}" ${checked}></td>
            <td>${hands} ${escapeHtml(c.name)}${junkTag}</td>
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
    const emptyFiltered = (!visible.length)
        ? '<tr class="empty"><td colspan="6">No captures match the current filters.</td></tr>' : '';
    capturesBody.innerHTML = emptyFiltered + rows;
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
    // Selection survives filter flips (Train uses the FULL set), so call out
    // selected captures the current filters are hiding.
    const n = selectedCaptures.size;
    const boxes = [...capturesBody.querySelectorAll('input.cap-check')];
    const visibleNames = new Set(boxes.map(b => b.dataset.name));
    const hiddenSel = [...selectedCaptures].filter(name => !visibleNames.has(name)).length;
    selStatus.textContent = `${n} selected` + (hiddenSel ? ` (${hiddenSel} filtered out)` : '');
    trainBtn.disabled = (n === 0);
    // Check-all reflects VISIBLE rows only: checked when every visible row is
    // selected, indeterminate when some are.
    const visSel = boxes.filter(b => selectedCaptures.has(b.dataset.name)).length;
    checkAll.checked = (boxes.length > 0 && visSel === boxes.length);
    checkAll.indeterminate = (visSel > 0 && visSel < boxes.length);
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

// PC TRAIN TRAP (board #0314-adjacent): a role-less /api/train on a two-hand
// capture builds the 120-feature bilateral-pivot model, which the per-band
// router can never run (separate-model-per-hand needs one 60-feature model
// per hand). The load guard rightly refuses it, so training LOOKED fine
// (r2 printed) while the ghosts kept running the OLD models. Mirror the VR
// TRAIN BOTH instead: any two-hand capture in the selection trains left,
// then right, each into its own per-hand engine slot.

async function postTrain(body) {
    const r = await fetch('/api/train', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await readError(r));
    return r.json();
}

// Loading a model does NOT auto-resume inference; say which state we're in
// instead of implying predictions are flowing while paused.
function trainRunHint() {
    return (inferenceState && inferenceState.enabled)
        ? 'predicting' : 'click ▶ Resume to run';
}

// Train -> see it live (the core-loop fix): after a successful activate,
// start predicting without a trip to the Predict button (mirrors the VR
// client's runTrain) and pulse the control-bar model chips.
async function autoEnablePrediction() {
    try {
        const r = await fetch('/api/inference/enabled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: true }),
        });
        // Optimistic local flip so trainRunHint() says "predicting" now
        // instead of one WS tick from now; the next tick confirms.
        if (r.ok && inferenceState) inferenceState.enabled = true;
    } catch (e) {
        console.warn('auto-enable after train failed', e);
    }
    if (predictModels) {
        predictModels.classList.add('just-trained');
        setTimeout(() => predictModels.classList.remove('just-trained'), 4000);
    }
}

trainBtn.onclick = async () => {
    if (selectedCaptures.size === 0) return;
    const captures = [...selectedCaptures];
    const label = captures.length === 1 ? captures[0] : `${captures.length} captures`;
    // Two-hand detection: /api/captures rows carry `hands` (2 = both roles in
    // the CSV). ANY two-hand capture in the mix means per-hand training.
    const byName = new Map(_lastCapturesList.map(c => [c.name, c]));
    const twoHand = captures.some(n => (byName.get(n) || {}).hands === 2);

    trainBtn.disabled = true;
    trainStatus.className = 'train-status busy';

    try {
        if (twoHand) {
            // Sequential on purpose: each RF fit is CPU-bound 20-60s; two at
            // once would just contend and blur the progress line.
            const results = {};
            for (const role of ['left', 'right']) {
                trainStatus.className = 'train-status busy';
                trainStatus.textContent = `⏳ Training ${role.toUpperCase()} hand on ${label}... (${role === 'left' ? '1' : '2'}/2)`;
                results[role] = await postTrain({ captures, role, activate: true });
            }
            const r2s = {
                left:  ((results.left.metrics  || {}).r2 ?? 0).toFixed(3),
                right: ((results.right.metrics || {}).r2 ?? 0).toFixed(3),
            };
            // Activation must be LOUD: active=false means the load guard (or
            // a load error) refused the model and the ghosts are still on the
            // old one. That silent gap cost two capture sessions.
            const failed = ['left', 'right'].filter(role => !results[role].active);
            if (failed.length) {
                const why = failed.map(role =>
                    `${role.toUpperCase()}: ${results[role].activate_error || 'not loaded'}`).join(' · ');
                trainStatus.className = 'train-status error';
                trainStatus.textContent = `⚠ Trained (L R²=${r2s.left}, R R²=${r2s.right}) but NOT loaded: ${why}`;
            } else {
                await autoEnablePrediction();
                trainStatus.className = 'train-status ok';
                trainStatus.textContent = `✓ Both hands trained + LOADED (L R²=${r2s.left}, R R²=${r2s.right}) · ${trainRunHint()}`;
            }
        } else {
            // All-single-band selection: today's pooled/role-less call.
            trainStatus.textContent = `⏳ Training on ${label}...`;
            const result = await postTrain({ captures, activate: true });
            const m = result.metrics || {};
            const r2 = (m.r2 ?? 0).toFixed(3);
            const mse = (m.mse ?? 0).toFixed(4);
            const nf  = m.n_features ?? '?';
            const nl  = m.n_labels ?? '?';
            const nt  = m.n_train ?? '?';
            if (!result.active) {
                // Trained-but-not-loaded is a FAILURE for live use: say why.
                trainStatus.className = 'train-status error';
                trainStatus.textContent = `⚠ Trained (R²=${r2}) but NOT loaded: ${result.activate_error || result.warning || 'activation refused'}`;
            } else if (result.warning) {
                // e.g. role-less bilateral pivot loading with no live band
                // connected: on disk and loaded, but it can't drive ghosts.
                trainStatus.className = 'train-status error';
                trainStatus.textContent = `⚠ Trained + loaded, but: ${result.warning}`;
            } else {
                await autoEnablePrediction();
                trainStatus.className = 'train-status ok';
                trainStatus.textContent = `✓ Trained on ${nt} rows · ${nf} features → ${nl} labels · R²=${r2} · MSE=${mse} [loaded · ${trainRunHint()}]`;
            }
        }
        await refreshModels();
    } catch (e) {
        trainStatus.className = 'train-status error';
        trainStatus.textContent = `✗ Train failed: ${e.message}`;
    } finally {
        trainBtn.disabled = (selectedCaptures.size === 0);
    }
};

// ---------- models panel ----------

// name -> wearer, for the predict-chip mismatch warning (a model trained on
// someone else's arm quietly underperforms; tint the chip instead).
const _modelWearerByName = new Map();

async function refreshModels() {
    try {
        const r = await fetch('/api/models');
        if (!r.ok) return;
        const list = await r.json();
        list.forEach(m => { if (m.name) _modelWearerByName.set(m.name, m.wearer || null); });
        renderModels(list);
    } catch (e) {
        // best-effort
    }
}

// SESSION VIEW for models (same rule as captures): with a session active the
// panel shows ONLY models trained in it (train_from_captures stamps
// session_id into each model's metadata.json), so 35 historical models can't
// bury the two you just trained. A toggle row reveals the rest on demand.
let showAllModels = false;
let _lastModelsList = [];

// Engine-slot signature from the WS snapshot (inference.active_models); a
// change re-triggers refreshModels from handleTick so badges track reality.
let _lastActiveModelsSig = null;

// Rebuild gate (same convention as renderDiscovery/_discoverySig): the table
// re-renders on a 10s poll plus every engine-slot tick, and rebuilding under
// the cursor would eat a "use" click. Skip the DOM rebuild unless the
// rendered data actually changed.
let _modelsSig = null;

function renderModels(list) {
    _lastModelsList = list;

    let working = list;
    let outsideCount = 0;
    if (activeSession) {
        const inSession = list.filter(m => m.session_id === activeSession.id);
        outsideCount = list.length - inSession.length;
        if (!showAllModels) working = inSession;
    }

    const sig = JSON.stringify([
        activeSession ? activeSession.id : null, showAllModels, outsideCount,
        working.map(m => [m.name, m.created, m.path, m.active,
                          (m.active_roles || []).join(','),
                          (m.metrics || {}).role || '', m.session_id || '']),
    ]);
    if (sig === _modelsSig) return;
    _modelsSig = sig;

    modelsCount.textContent = (activeSession && !showAllModels)
        ? `${working.length} of ${list.length} model${list.length === 1 ? '' : 's'} (this session)`
        : `${list.length} model${list.length === 1 ? '' : 's'}`;
    if (!list.length) {
        modelsBody.innerHTML = '<tr class="empty"><td colspan="6">No models trained yet.</td></tr>';
        return;
    }

    const rows = working.map(m => {
        const metrics = m.metrics || {};
        const r2  = (metrics.r2 ?? null);
        const mse = (metrics.mse ?? null);
        const nf  = metrics.n_features ?? '?';
        const nl  = metrics.n_labels ?? '?';
        const r2s  = (r2 !== null && !isNaN(r2)) ? Number(r2).toFixed(3) : '—';
        const mses = (mse !== null && !isNaN(mse)) ? Number(mse).toFixed(4) : '—';
        const created = m.created ?? '';
        // Every engine slot this model occupies (shared and/or left/right);
        // legacy `active` fallback keeps old payloads rendering.
        const slots = m.active_roles || (m.active ? ['shared'] : []);
        const slotTags = slots.filter(s => s !== 'shared')
            .map(s => ' · ' + s[0].toUpperCase()).join('');
        const activeBadge = slots.length
            ? `<span class="badge-active" title="loaded in: ${slots.join(', ')}">active${slotTags}</span>`
            : '';
        // Per-hand role badge (separate-model-per-hand). Reuses the capture
        // list's 1H/2H badge styling: green = left, orange = right.
        const role = (metrics.role === 'left' || metrics.role === 'right')
            ? metrics.role : null;
        const roleBadge = role
            ? `<span class="hands-badge ${role === 'left' ? 'h2' : 'h1'}" title="single-arm model, trained on role=${role} rows">${role === 'left' ? 'L' : 'R'}</span>`
            : '';
        const escName = escapeHtml(m.name || '');
        const escPath = escapeHtml(m.path || '');
        // Role-tagged models must load into their OWN per-hand slot; sending
        // them to the shared endpoint leaves the router on the old model.
        const useBtn = slots.length ? '' :
            `<button class="link" data-activate="${escPath}" data-role="${role || ''}">use</button>`;
        return `<tr>
            <td>${roleBadge} ${escName} ${activeBadge}</td>
            <td>${escapeHtml(created)}</td>
            <td>${r2s}</td>
            <td>${mses}</td>
            <td>${nf} × ${nl}</td>
            <td class="actions">${useBtn}</td>
        </tr>`;
    }).join('');

    const emptySession = (!working.length)
        ? '<tr class="empty"><td colspan="6">No models trained in this session yet · select captures and hit Train.</td></tr>' : '';
    const toggleRow = (activeSession && (outsideCount > 0 || showAllModels))
        ? `<tr class="show-all-row"><td colspan="6"><button class="link" id="models-show-all">${
            showAllModels ? '▾ hide models outside this session'
                          : `▸ show all ${list.length} models`
          }</button></td></tr>` : '';
    modelsBody.innerHTML = emptySession + rows + toggleRow;

    const showAllBtn = document.getElementById('models-show-all');
    if (showAllBtn) showAllBtn.onclick = () => {
        showAllModels = !showAllModels;
        renderModels(_lastModelsList);
    };

    modelsBody.querySelectorAll('button[data-activate]').forEach(btn => {
        btn.onclick = async () => {
            const path = btn.dataset.activate;
            const role = btn.dataset.role;
            const endpoint = (role === 'left' || role === 'right')
                ? `/api/inference/model/${role}` : '/api/inference/model';
            try {
                const r = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path }),
                });
                if (!r.ok) throw new Error(await readError(r));
                await refreshModels();
            } catch (e) {
                // Surface the load guard's refusal (e.g. "expects 120
                // features but the connected band has 60 cells") instead of
                // failing silently like the train trap did.
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
// Paired per-finger pistons: GT + predicted live side by side inside
// #comparator-fingers (the old #lask-bars / #inference-bars columns).
const laskBars = document.querySelectorAll('#comparator-fingers .piston.gt');
const comparatorFingers = document.getElementById('comparator-fingers');
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

// BAR fill fraction. LASK5 wire convention (board #0317): pressed piston ->
// 0.0 on the wire, so 1.0 = released = finger extended. Tory reads "bar
// filled = finger curled", so wire-space (0..1) values invert for DISPLAY
// only; the .piston-val text keeps the raw wire number so CSV/log
// cross-checks still match. Legacy raw ADC (>1.5) keeps the direct
// proportional fill via pistonFraction (never double-transform).
function pistonFillFraction(v) {
    if (typeof v !== 'number' || !isFinite(v)) return 0;
    // Wire-space (predictions can overshoot [0,1] slightly): clamp, invert.
    if (v >= -0.5 && v <= 1.5) return 1 - Math.min(Math.max(v, 0), 1);
    return pistonFraction(v);
}

function renderLask(dev) {
    // Flexion bars show whenever LASK5 streams, alongside the hand panels if
    // VR is also live (a VR session with a LASK5 plugged in shows both).
    const comparator = document.querySelector('.comparator');
    const hasLask = !!(dev && Array.isArray(dev.values) && dev.values.length);
    if (comparator) comparator.classList.toggle('has-lask', hasLask);
    if (!hasLask) {
        laskMeta.textContent = 'no device';
        // zero the bars
        laskBars.forEach(p => {
            p.querySelector('.piston-fill').style.height = '0%';
            p.querySelector('.piston-val').textContent = '--';
        });
        drawJoystick(null);
        return;
    }
    laskMeta.textContent =
        `${dev.device_id} · ${dev.hz.toFixed(1)} Hz · ${dev.packets} pkts`;
    const vals = dev.values;
    laskBars.forEach(p => {
        const i = parseInt(p.dataset.i, 10);
        const v = i < vals.length ? vals[i] : 0;
        const pct = pistonFillFraction(v) * 100;
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
const inferenceBars   = document.querySelectorAll('#comparator-fingers .piston.pred');
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
        if (comparatorFingers) comparatorFingers.classList.add('dimmed');
        inferenceBars.forEach(p => {
            p.querySelector('.piston-fill').style.height = '0%';
            p.querySelector('.piston-val').textContent = '--';
        });
        return;
    }
    if (comparatorFingers) comparatorFingers.classList.remove('dimmed');
    inferenceMeta.innerHTML = escapeHtml(inf.model || 'live') + recLiveSpan;
    const vals = inf.piston_values;
    inferenceBars.forEach(p => {
        const i = parseInt(p.dataset.i, 10);
        const v = i < vals.length ? vals[i] : 0;
        // Same wire-space bar inversion as the GT pistons: training consumes
        // wire-space label_*, so predictions are wire-space too.
        const pct = pistonFillFraction(v) * 100;
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

// Control-bar model chips: which model each engine slot is running, always
// visible next to the Predict toggle. Rebuilt in place, sig-gated so ticks
// with unchanged slots leave the DOM alone.
let _predictChipsSig = null;

function renderPredictModelChips(inf) {
    if (!predictModels) return;
    const am = (inf && inf.active_models) || {};
    const live = !!(inf && inf.enabled) && inf.status === 'live';
    const sessionWearer = (activeSession && activeSession.wearer) || null;
    const entries = [];
    if (am.left)  entries.push(['L ', am.left]);
    if (am.right) entries.push(['R ', am.right]);
    if (!entries.length && am.shared) entries.push(['', am.shared]);
    const sig = JSON.stringify([am, inf && inf.status, !!(inf && inf.enabled),
        sessionWearer, entries.map(([, n]) => _modelWearerByName.get(n) || null)]);
    if (sig === _predictChipsSig) return;
    _predictChipsSig = sig;
    if (!entries.length) {
        predictModels.innerHTML = '<span class="model-chip empty">no model, train first</span>';
        return;
    }
    const stateCls = live ? 'on' : 'paused';
    predictModels.innerHTML = entries.map(([badge, name]) => {
        // Warn when the model was trained on a different wearer than the
        // active session's: it will quietly underperform on this arm.
        const wearer = _modelWearerByName.get(name) || null;
        const mismatch = !!(sessionWearer && wearer && wearer !== sessionWearer);
        const title = mismatch
            ? `trained on ${wearer}, session wearer is ${sessionWearer}`
            : ((inf && inf.status) || '');
        return `<span class="model-chip ${stateCls}${mismatch ? ' wearer-mismatch' : ''}"`
            + ` title="${escapeHtml(title)}">${escapeHtml(badge + name)}</span>`;
    }).join('');
}

function renderInferenceControls(inf) {
    maybeRestoreHandTarget(inf);
    renderPredictModelChips(inf);

    const hasModel = !!(inf && inf.model);
    const enabled  = !!(inf && inf.enabled);

    // --- toggle button ---
    inferToggleBtn.disabled = !hasModel;
    inferToggleBtn.classList.toggle('running', enabled);
    inferToggleBtn.classList.toggle('paused', hasModel && !enabled);
    if (!hasModel)      inferToggleBtn.textContent = '▶ Predict (no model — train first)';
    else if (enabled)   inferToggleBtn.textContent = '⏸ Pause prediction';
    else                inferToggleBtn.textContent = '▶ Predict';
    inferToggleBtn.title = hasModel
        ? (enabled ? 'Prediction is LIVE — click to pause' : 'Click to start predicting')
        : 'No model loaded: check a capture below and hit ⚙ Train (Stage 3), or pick one in the Models panel';

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
    const deltaRows = document.querySelectorAll('#comparator-fingers .delta-row');
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

// ---------- debug dashboard (--debug mode) ----------
// Unlocked by GET /api/mode. Surfaces the raw per-device truth for a recording
// / troubleshooting session: stream health, per-channel matrix stats, IMU raw
// counts (fused orientation is the Orientation widget in Stage 1), forearm
// roll/palm-up derived from Quest hand joints, and a raw-frame inspector.

async function initDebugMode() {
    try {
        const r = await fetch('/api/mode');
        if (r.ok) {
            const { debug } = await r.json();
            debugMode = !!debug;
            document.body.classList.toggle('debug', debugMode);
        }
    } catch (e) {
        // Older servers may not expose /api/mode; stay in normal mode.
    }
    const freeze = document.getElementById('debug-insp-freeze');
    if (freeze) freeze.onchange = () => { debugFreeze = freeze.checked; };
}

// --- forearm orientation from Quest hand joints (JS port of forearm.py) ---
// Gravity-relative roll (0 = palm-up) + palm_up flag, from the wrist/knuckle
// joint POSITIONS (not the wrist quaternion), so it matches the disk-written
// forearm_roll_deg / palm_up columns without a firmware dependency.
const _FA_WRIST = 0, _FA_MIDDLE_MCP = 10, _FA_INDEX = 6, _FA_PINKY = 21, _FA_MIN = 22;
function _faSub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function _faDot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
function _faCross(a, b) {
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}
function _faNorm(a) {
    const m = Math.sqrt(_faDot(a, a));
    return m > 1e-9 ? [a[0] / m, a[1] / m, a[2] / m] : [0, 0, 0];
}
function forearmFromQuest(dev) {
    const values = dev && dev.values;
    if (!Array.isArray(values) || values.length < _FA_MIN * 7) return null;
    const P = [];
    for (let i = 0; i < Math.floor(values.length / 7); i++) {
        P.push([values[i * 7], values[i * 7 + 1], values[i * 7 + 2]]);
    }
    if (P.length < _FA_MIN) return null;
    const axis = _faNorm(_faSub(P[_FA_MIDDLE_MCP], P[_FA_WRIST]));  // hand long axis
    if (axis[0] === 0 && axis[1] === 0 && axis[2] === 0) return null;
    const handed = (dev.role === 'left') ? 'left' : 'right';
    let n = _faCross(_faSub(P[_FA_INDEX], P[_FA_WRIST]), _faSub(P[_FA_PINKY], P[_FA_WRIST]));
    if (handed !== 'left') n = [-n[0], -n[1], -n[2]];     // point OUT of the palm
    const pn = _faNorm(n);
    const up = [0, 1, 0];
    const proj = (v) => _faNorm(_faSub(v, axis.map(x => x * _faDot(v, axis))));
    const f = proj(up), t = proj(pn);
    const rollRad = Math.atan2(_faDot(axis, _faCross(f, t)), _faDot(f, t));
    return { roll_deg: rollRad * 180 / Math.PI, palm_up: _faDot(pn, up) > 0 };
}

function matrixStats(matrix) {
    // matrix is [cols][rows]; count cells above the heatmap noise gate + max/mean.
    if (!Array.isArray(matrix) || !matrix.length) return null;
    let max = 0, sum = 0, cells = 0, active = 0;
    for (const col of matrix) {
        for (const v of col) {
            cells++; sum += v;
            if (v > max) max = v;
            if (v >= HEATMAP_NOISE_GATE) active++;
        }
    }
    return { active, cells, max, mean: cells ? Math.round(sum / cells) : 0 };
}

function renderDebugPanel() {
    const cardsEl = document.getElementById('debug-cards');
    if (!cardsEl) return;
    if (!lastDevices.length) {
        cardsEl.innerHTML = '<div class="empty">Waiting for a device…</div>';
    } else {
        cardsEl.innerHTML = lastDevices.map(d => {
            const stale = d.last_seen_age > 2.0;
            const sub = (d.subscribed === true)
                ? '<span class="dbg-ok">sub ✓</span>'
                : (d.sub_error
                    ? `<span class="dbg-bad">sub ✗ ${escapeHtml(String(d.sub_error))}</span>`
                    : '<span class="dbg-muted">unsub</span>');
            const role = d.role
                ? `<span class="dbg-role dbg-role-${escapeHtml(d.role)}">${escapeHtml(d.role)}</span>` : '';
            const rows = [];
            rows.push(`<div class="dbg-line"><span>stream</span><b class="${stale ? 'dbg-bad' : 'dbg-ok'}">`
                + `${d.hz.toFixed(1)} Hz · ${stale ? d.last_seen_age.toFixed(1) + 's stale' : 'live'}</b>`
                + ` · ${d.packets} pkts · ${sub}</div>`);
            const ms = matrixStats(d.matrix);
            if (ms) rows.push(`<div class="dbg-line"><span>channels</span><b>${ms.active}/${ms.cells}</b>`
                + ` active · max ${ms.max} · mean ${ms.mean}</div>`);
            if (d.imu && Array.isArray(d.imu.gyro) && Array.isArray(d.imu.accel)) {
                const scale = d.imu_scale ? '<span class="dbg-ok">scale ✓</span>' : '<span class="dbg-muted">no scale</span>';
                rows.push(`<div class="dbg-line"><span>imu raw</span>g ${d.imu.gyro.join(',')} · a ${d.imu.accel.join(',')} · ${scale}</div>`);
            }
            const fa = (d.device_type === 'quest_hand') ? forearmFromQuest(d) : null;
            if (fa) rows.push(`<div class="dbg-line"><span>forearm</span><b>${fa.roll_deg.toFixed(1)}°</b> · palm ${fa.palm_up ? 'UP' : 'down'}</div>`);
            const st = d.status || {};
            const bits = [];
            if (typeof st.vbat === 'number') bits.push(`${st.vbat.toFixed(2)}V`);
            if (typeof st.pct === 'number') bits.push(`${st.pct}%`);
            if (typeof st.rssi === 'number') bits.push(`${st.rssi}dBm`);
            if (typeof st.uptime_s === 'number') bits.push(formatUptime(st.uptime_s));
            if (d.reboot_count) bits.push(`⟳${d.reboot_count}${d.last_reset_cause ? ' ' + d.last_reset_cause : ''}`);
            if (bits.length) rows.push(`<div class="dbg-line"><span>device</span>${escapeHtml(bits.join(' · '))}</div>`);
            const selCls = (d.device_id === selectedDeviceId) ? ' selected' : '';
            return `<div class="debug-card${selCls}${stale ? ' stale' : ''}" data-id="${escapeHtml(d.device_id)}">
                <div class="dbg-head"><b>${escapeHtml(d.device_id)}</b> ${role}`
                + ` <span class="dbg-muted">${escapeHtml(d.device_type)} ${d.rows}×${d.cols}</span></div>
                ${rows.join('')}
            </div>`;
        }).join('');
        cardsEl.querySelectorAll('.debug-card').forEach(el => {
            // _bandCardsSig reset moves the band-card highlight next tick
            // (renderDevices is gone; the cards live beside the heatmaps now).
            el.onclick = () => { selectedDeviceId = el.dataset.id; _bandCardsSig = null; renderDebugPanel(); };
        });
    }
    // Raw-frame inspector for the selected device (freeze pauses it for reading).
    if (debugFreeze) return;
    const dev = selectedDevice() || lastDevices[0];
    const devEl = document.getElementById('debug-insp-dev');
    const jsonEl = document.getElementById('debug-insp-json');
    if (!jsonEl) return;
    if (!dev) {
        if (devEl) devEl.textContent = 'no device';
        jsonEl.textContent = '(no device streaming)';
        return;
    }
    if (devEl) devEl.textContent = dev.device_id;
    // Summarize the bulky matrix so the JSON stays readable; keep the rest raw.
    const view = Object.assign({}, dev);
    if (Array.isArray(view.matrix)) {
        const ms = matrixStats(view.matrix);
        view.matrix = `[${view.matrix.length}×${(view.matrix[0] || []).length}] active=${ms.active} max=${ms.max}`;
    }
    jsonEl.textContent = JSON.stringify(view, null, 2);
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

initDebugMode();
connectWS();

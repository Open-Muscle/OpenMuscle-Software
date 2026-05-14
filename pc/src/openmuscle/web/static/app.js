// OpenMuscle Web UI — live heatmap, recording, capture management.
// Single-page vanilla JS. No bundler, no framework.

const wsStatus    = document.getElementById('ws-status');
const deviceList  = document.getElementById('device-list');
const canvas      = document.getElementById('heatmap');
const ctx         = canvas.getContext('2d');
const heatmapMeta = document.getElementById('heatmap-meta');
const recordBtn   = document.getElementById('record-btn');
const recordStatus= document.getElementById('record-status');
const captureName = document.getElementById('capture-name');
const capturesBody= document.getElementById('captures-body');

let selectedDeviceId = null;
let lastDevices = [];
let recordingState = null;        // null when idle; {filename, rows, duration_s} when recording

// ---------- WebSocket ----------

function connectWS() {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/live`);

    ws.onopen = () => {
        wsStatus.textContent = 'connected';
        wsStatus.className = 'badge online';
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
    renderDevices();
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

function renderRecording() {
    if (recordingState) {
        recordBtn.textContent = '■ Stop recording';
        recordBtn.classList.add('recording');
        recordStatus.textContent =
            `${recordingState.filename} · ${recordingState.rows} rows · ${recordingState.duration_s}s`;
    } else {
        recordBtn.textContent = '● Start recording';
        recordBtn.classList.remove('recording');
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
            const dev = selectedDevice();
            if (!dev) { alert('No active device.'); return; }
            const r = await fetch('/api/recording', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    device_id: dev.device_id,
                    filename: captureName.value.trim() || null,
                }),
            });
            if (!r.ok) throw new Error(await readError(r));
            captureName.value = '';
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

function renderCaptures(list) {
    if (!list.length) {
        capturesBody.innerHTML = '<tr class="empty"><td colspan="4">No captures saved yet.</td></tr>';
        return;
    }
    const rows = list.map(c => {
        const date = new Date(c.mtime * 1000).toLocaleString();
        const kb = (c.size_bytes / 1024).toFixed(1);
        return `<tr>
            <td>${escapeHtml(c.name)}</td>
            <td>${kb} KB</td>
            <td>${escapeHtml(date)}</td>
            <td class="actions">
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
}

// ---------- LASK5 piston bars + joystick ----------

// Calibrated range for LASK5 piston ADC readings. Could be made dynamic
// per-device once we surface mins/maxes from settings; 0..4095 covers raw.
const LASK_VMIN = 0;
const LASK_VMAX = 4095;
const laskMeta = document.getElementById('lask-meta');
const laskBars = document.getElementById('lask-bars');
const joyCanvas = document.getElementById('joystick-canvas');
const joyCtx = joyCanvas.getContext('2d');
const joyVals = document.getElementById('joystick-vals');

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
        const pct = Math.max(0, Math.min(100,
            ((v - LASK_VMIN) / (LASK_VMAX - LASK_VMIN)) * 100));
        p.querySelector('.piston-fill').style.height = pct.toFixed(1) + '%';
        p.querySelector('.piston-val').textContent = String(v);
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

const inferenceMeta = document.getElementById('inference-meta');
const inferenceBars = document.getElementById('inference-bars');

function renderInference(inf) {
    if (!inf) {
        inferenceMeta.textContent = 'no model loaded';
        return;
    }
    if (!inf.available || !Array.isArray(inf.piston_values)) {
        inferenceMeta.textContent = inf.status || 'no model loaded';
        inferenceBars.classList.add('dimmed');
        inferenceBars.querySelectorAll('.piston').forEach(p => {
            p.querySelector('.piston-fill').style.height = '0%';
            p.querySelector('.piston-val').textContent = '--';
        });
        return;
    }
    inferenceBars.classList.remove('dimmed');
    inferenceMeta.textContent = inf.model || 'live';
    const vals = inf.piston_values;
    inferenceBars.querySelectorAll('.piston').forEach(p => {
        const i = parseInt(p.dataset.i, 10);
        const v = i < vals.length ? vals[i] : 0;
        const pct = Math.max(0, Math.min(100,
            ((v - LASK_VMIN) / (LASK_VMAX - LASK_VMIN)) * 100));
        p.querySelector('.piston-fill').style.height = pct.toFixed(1) + '%';
        p.querySelector('.piston-val').textContent = String(v);
    });
}

// ---------- utils ----------

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

// Refresh captures list once on load and whenever a recording stops
refreshCaptures();
setInterval(refreshCaptures, 5000);

connectWS();

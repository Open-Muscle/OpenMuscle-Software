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

function drawHeatmap(dev) {
    const matrix = dev.matrix;  // [cols][rows]
    if (!matrix || !matrix.length) return;
    const cols = matrix.length;
    const rows = matrix[0].length;

    heatmapMeta.textContent = `${dev.device_id} · ${rows}×${cols} · ${dev.hz.toFixed(1)} Hz · ${dev.packets} pkts`;

    // Resize canvas to fit the matrix aspect ratio nicely
    const w = canvas.clientWidth;
    const h = Math.max(120, Math.floor(w * (rows / cols) * 1.2));
    if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
    }

    const cellW = w / cols;
    const cellH = h / rows;

    ctx.fillStyle = '#0b0d12';
    ctx.fillRect(0, 0, w, h);

    for (let c = 0; c < cols; c++) {
        for (let r = 0; r < rows; r++) {
            const v = matrix[c][r];
            ctx.fillStyle = pressureColor(v);
            ctx.fillRect(c * cellW, r * cellH, cellW - 1, cellH - 1);
            if (v >= 500) {
                ctx.fillStyle = v > 2500 ? '#000' : '#fff';
                ctx.font = `${Math.floor(Math.min(cellW, cellH) * 0.32)}px monospace`;
                ctx.textBaseline = 'middle';
                ctx.textAlign = 'center';
                ctx.fillText(v, c * cellW + cellW / 2, r * cellH + cellH / 2);
            }
        }
    }
}

// Plasma-ish color ramp tuned for 0..4096 ADC values
function pressureColor(v) {
    const t = Math.max(0, Math.min(1, v / 4096));
    // Stops: dark navy -> magenta -> orange -> yellow
    const stops = [
        [11, 13, 18],
        [60, 9, 102],
        [156, 23, 158],
        [225, 100, 98],
        [253, 199, 98],
        [240, 249, 33],
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

recordBtn.onclick = async () => {
    try {
        if (recordingState) {
            const r = await fetch('/api/recording', { method: 'DELETE' });
            if (!r.ok) throw new Error(await r.text());
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
            if (!r.ok) {
                const err = await r.json().catch(() => ({}));
                throw new Error(err.detail || 'Failed to start recording');
            }
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

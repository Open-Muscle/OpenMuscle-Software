// OpenMuscle webcam hand labeler: page logic.
//
// Tracks a hand with the computer's camera and streams it to /ws/webcam in the
// same payload shape the Quest client uses on /ws/quest, so the server sees a
// label device of type "webcam_hand" (ids webcam-left / webcam-right). The
// camera image never leaves this page; only joint coordinates are sent.
//
// The geometry (21 tracker landmarks -> 25 WebXR-order joints in a hand-local
// frame, and the finger-curl numbers shown on the bars) lives in hand-map.js.

import { toWebXRJoints, fingerFlexion, landmarksToArrays, SYNTHESIZED_JOINTS,
         METACARPAL_FRACTION } from './hand-map.js';

// Hand tracker: MediaPipe Hand Landmarker (Apache-2.0), loaded at run time.
// A copy placed under /static/vendor/mediapipe/ is preferred when present so
// the page also works with no internet; otherwise the public CDN is used.
//   vendor/mediapipe/vision_bundle.mjs
//   vendor/mediapipe/wasm/            (the package's wasm folder)
//   vendor/mediapipe/hand_landmarker.task
const TRACKER_VERSION = '0.10.14';
const CDN_BASE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${TRACKER_VERSION}`;
const CDN_MODEL = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task';
const LOCAL_BASE = '/static/vendor/mediapipe';

const SEND_HZ = 30;             // same throttle as the Quest client
const FINGERS = ['thumb', 'index', 'middle', 'ring', 'pinky'];

// Bone list for the on-screen skeleton (tracker landmark indices).
const BONES = [
    [0, 1], [1, 2], [2, 3], [3, 4],
    [0, 5], [5, 6], [6, 7], [7, 8],
    [5, 9], [9, 10], [10, 11], [11, 12],
    [9, 13], [13, 14], [14, 15], [15, 16],
    [13, 17], [17, 18], [18, 19], [19, 20], [0, 17],
];

const video = document.getElementById('video');
const overlay = document.getElementById('overlay');
const hint = document.getElementById('hint');
const cameraSelect = document.getElementById('camera');
const handSelect = document.getElementById('hand');
const startBtn = document.getElementById('start');
const barsEl = document.getElementById('bars');
const statsEl = document.getElementById('stats');
const pillTracker = document.getElementById('pill-tracker');
const pillServer = document.getElementById('pill-server');
const pillHand = document.getElementById('pill-hand');

let tracker = null;
let trackerAssets = '';
let stream = null;
let running = false;
let ws = null;
let lastVideoTime = -1;
let lastSendAt = 0;
let framesSent = 0;
let detectTimes = [];

function setPill(el, text, cls) {
    el.textContent = text;
    el.className = 'pill' + (cls ? ' ' + cls : '');
}

// ---------- finger-curl bars ----------

const barFills = {};
const barVals = {};
for (const f of FINGERS) {
    const name = document.createElement('div');
    name.className = 'name';
    name.textContent = f;
    const track = document.createElement('div');
    track.className = 'track';
    const fill = document.createElement('div');
    fill.className = 'fill';
    track.appendChild(fill);
    const val = document.createElement('div');
    val.className = 'val';
    val.textContent = '-';
    barsEl.append(name, track, val);
    barFills[f] = fill;
    barVals[f] = val;
}

function showFlexion(flex) {
    for (const f of FINGERS) {
        const v = flex ? flex[f] : null;
        barFills[f].style.width = v == null ? '0%' : `${Math.round(v * 100)}%`;
        barVals[f].textContent = v == null ? '-' : v.toFixed(2);
    }
}

// ---------- server socket ----------

function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/webcam`);
    ws.onopen = () => setPill(pillServer, 'server: connected', 'ok');
    ws.onclose = () => {
        setPill(pillServer, 'server: reconnecting', 'warn');
        setTimeout(connect, 1500);
    };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* closing anyway */ } };
}

// ---------- tracker ----------

async function loadTracker() {
    if (tracker) return;
    setPill(pillTracker, 'tracker: loading', 'warn');
    let base = CDN_BASE;
    let model = CDN_MODEL;
    trackerAssets = 'cdn';
    try {
        const r = await fetch(`${LOCAL_BASE}/vision_bundle.mjs`, { method: 'HEAD' });
        if (r.ok) {
            base = LOCAL_BASE;
            model = `${LOCAL_BASE}/hand_landmarker.task`;
            trackerAssets = 'local';
        }
    } catch (e) { /* no local copy: use the CDN */ }
    const vision = await import(`${base}/vision_bundle.mjs`);
    const files = await vision.FilesetResolver.forVisionTasks(`${base}/wasm`);
    const options = delegate => ({
        baseOptions: { modelAssetPath: model, delegate },
        runningMode: 'VIDEO',
        numHands: 2,
        minHandDetectionConfidence: 0.5,
        minHandPresenceConfidence: 0.5,
        minTrackingConfidence: 0.5,
    });
    try {
        tracker = await vision.HandLandmarker.createFromOptions(files, options('GPU'));
    } catch (e) {
        // No usable graphics acceleration: the tracker also runs on the CPU.
        tracker = await vision.HandLandmarker.createFromOptions(files, options('CPU'));
    }
    setPill(pillTracker, `tracker: ready (${trackerAssets})`, 'ok');
}

// ---------- camera ----------

async function listCameras() {
    try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const cams = devices.filter(d => d.kind === 'videoinput');
        const current = cameraSelect.value;
        cameraSelect.innerHTML = '<option value="">default camera</option>';
        cams.forEach((c, i) => {
            const o = document.createElement('option');
            o.value = c.deviceId;
            o.textContent = c.label || `camera ${i + 1}`;
            cameraSelect.appendChild(o);
        });
        if ([...cameraSelect.options].some(o => o.value === current)) cameraSelect.value = current;
    } catch (e) { /* the list is a convenience; the default camera still works */ }
}

async function startCamera() {
    const id = cameraSelect.value;
    stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
            deviceId: id ? { exact: id } : undefined,
            width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30 },
        },
    });
    video.srcObject = stream;
    await video.play();
    overlay.width = video.videoWidth || 640;
    overlay.height = video.videoHeight || 480;
    await listCameras();   // labels are only available once permission is granted
}

function stopCamera() {
    if (stream) stream.getTracks().forEach(t => t.stop());
    stream = null;
    video.srcObject = null;
}

// ---------- per-frame work ----------

// The tracker names hands as if the picture were a mirror image. We feed it the
// camera's unflipped frames, so its "Left" is the person's right hand.
function actualSide(trackerLabel) {
    return String(trackerLabel).toLowerCase() === 'left' ? 'right' : 'left';
}

// Decide which detected hands to send and what side each one is.
// Returns [{index, side, score}], at most one entry per side.
function chooseHands(result) {
    const handed = result.handedness || result.handednesses || [];
    const found = (result.worldLandmarks || []).map((_, i) => {
        const cat = (handed[i] && handed[i][0]) || {};
        return { index: i, side: actualSide(cat.categoryName), score: cat.score || 0 };
    });
    if (!found.length) return [];
    const pick = handSelect.value;
    if (pick === 'left' || pick === 'right') {
        // The operator said which hand is in view: take the clearest one and
        // call it that, whatever the tracker guessed.
        const best = found.reduce((a, b) => (b.score > a.score ? b : a));
        return [{ ...best, side: pick }];
    }
    const bySide = {};
    for (const h of found) {
        if (!bySide[h.side] || h.score > bySide[h.side].score) bySide[h.side] = h;
    }
    return Object.values(bySide);
}

function drawOverlay(result) {
    const ctx = overlay.getContext('2d');
    const w = overlay.width;
    const h = overlay.height;
    ctx.clearRect(0, 0, w, h);
    for (const lm of result.landmarks || []) {
        ctx.strokeStyle = 'rgba(102, 204, 255, 0.9)';
        ctx.lineWidth = 2;
        for (const [a, b] of BONES) {
            ctx.beginPath();
            ctx.moveTo(lm[a].x * w, lm[a].y * h);
            ctx.lineTo(lm[b].x * w, lm[b].y * h);
            ctx.stroke();
        }
        ctx.fillStyle = '#ffffff';
        for (const p of lm) {
            ctx.beginPath();
            ctx.arc(p.x * w, p.y * h, 3, 0, Math.PI * 2);
            ctx.fill();
        }
    }
}

function frameMeta() {
    return {
        tracker: 'mediapipe-hand-landmarker',
        tracker_version: TRACKER_VERSION,
        tracker_assets: trackerAssets,
        frame: 'hand_local',
        axes: 'origin wrist; y wrist to middle knuckle; x toward the index/thumb side; z = x cross y; meters',
        rotations: 'identity (not measured by a camera)',
        synthesized_joints: SYNTHESIZED_JOINTS,
        metacarpal_fraction: METACARPAL_FRACTION,
    };
}

function onResult(result, nowMs) {
    drawOverlay(result);
    const hands = chooseHands(result);
    if (!hands.length) {
        setPill(pillHand, 'no hand', '');
        showFlexion(null);
        return;
    }
    const due = nowMs - lastSendAt >= 1000 / SEND_HZ;
    const open = ws && ws.readyState === WebSocket.OPEN;
    let shown = false;
    const labels = [];
    for (const h of hands) {
        const joints = toWebXRJoints(landmarksToArrays(result.worldLandmarks[h.index]));
        if (!joints) continue;   // hand edge-on or collapsed: skip this frame
        labels.push(h.side);
        if (!shown) { showFlexion(fingerFlexion(joints)); shown = true; }
        if (due && open) {
            ws.send(JSON.stringify({
                device_id: `webcam-${h.side}`,
                ts: Math.floor(nowMs),
                handedness: h.side,
                joints,
                meta: frameMeta(),
            }));
            framesSent++;
        }
    }
    if (due && open && labels.length) lastSendAt = nowMs;
    setPill(pillHand, labels.length ? `tracking: ${labels.join(' + ')}` : 'hand unclear',
            labels.length ? 'ok' : 'warn');
}

function loop() {
    if (!running) return;
    if (video.readyState >= 2 && video.currentTime !== lastVideoTime) {
        lastVideoTime = video.currentTime;
        const now = performance.now();
        const result = tracker.detectForVideo(video, now);
        onResult(result, now);
        detectTimes.push(now);
        while (detectTimes.length && now - detectTimes[0] > 1000) detectTimes.shift();
        statsEl.textContent = `${detectTimes.length} frames/s tracked · ${framesSent} frames sent`;
    }
    requestAnimationFrame(loop);
}

// ---------- start / stop ----------

async function start() {
    startBtn.disabled = true;
    try {
        hint.textContent = 'Loading the hand tracker...';
        await loadTracker();
        hint.textContent = 'Waiting for the camera...';
        await startCamera();
        hint.style.display = 'none';
        running = true;
        startBtn.textContent = 'Stop camera';
        requestAnimationFrame(loop);
    } catch (e) {
        hint.style.display = 'flex';
        hint.textContent = `Could not start: ${e.message || e}`;
        if (!tracker) setPill(pillTracker, 'tracker: failed to load', 'bad');
        stopCamera();
    } finally {
        startBtn.disabled = false;
    }
}

function stop() {
    running = false;
    stopCamera();
    overlay.getContext('2d').clearRect(0, 0, overlay.width, overlay.height);
    hint.style.display = 'flex';
    hint.textContent = 'Camera stopped.';
    startBtn.textContent = 'Start camera';
    setPill(pillHand, 'no hand', '');
    showFlexion(null);
    statsEl.textContent = 'not running';
}

startBtn.onclick = () => (running ? stop() : start());
cameraSelect.onchange = async () => {
    if (!running) return;
    stopCamera();
    try { await startCamera(); } catch (e) { stop(); hint.textContent = `Could not switch camera: ${e.message || e}`; }
};

if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    hint.textContent = 'This browser cannot use a camera on this address. Open the page as http://localhost:8000/webcam on the computer that has the camera.';
    startBtn.disabled = true;
}

showFlexion(null);
connect();
listCameras();

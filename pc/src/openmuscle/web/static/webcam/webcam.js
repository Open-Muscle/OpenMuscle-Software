// OpenMuscle webcam hand labeler: page logic.
//
// Tracks a hand with the computer's camera and streams it to /ws/webcam in the
// same payload shape the Quest client uses on /ws/quest, so the server sees a
// label device of type "webcam_hand" (ids webcam-left / webcam-right). The
// camera image never leaves this page; only joint coordinates are sent.
//
// The geometry (21 tracker landmarks -> 25 WebXR-order joints in a hand-local
// frame, and the finger-curl numbers shown on the bars) lives in hand-map.js.
// Which hand is which, the failure policy and the plain error wording live in
// track-logic.js (unit tested under Node).
//
// The page runs two ways: on its own at /webcam, or inside the dashboard's
// Camera panel as /webcam?embed=1. The panel is the recommended way: browsers
// stop drawing frames for a tab in the background, which pauses tracking, and
// inside the dashboard the camera and the Record button share one visible tab.

import { toWebXRJoints, fingerFlexion, landmarksToArrays, SYNTHESIZED_JOINTS,
         METACARPAL_FRACTION } from './hand-map.js';
import { HandSelector, newFailureState, onDetectOk, onDetectError, detectOS,
         describeError, cameraPageUrl, unsupportedText, GPU_FALLBACK_NOTE
       } from './track-logic.js';

// Hand tracker: MediaPipe Hand Landmarker (Apache-2.0), loaded at run time.
// A copy fetched with `openmuscle assets fetch` (served under /static/vendor/
// mediapipe/) is preferred when the server says it is complete and verified, so
// the page also works with no internet; otherwise, or if that copy fails to
// load, the public CDN is used.
//   vendor/mediapipe/vision_bundle.mjs
//   vendor/mediapipe/wasm/            (the package's wasm folder)
//   vendor/mediapipe/hand_landmarker.task
const TRACKER_VERSION = '0.10.14';
const CDN_BASE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${TRACKER_VERSION}`;
const CDN_MODEL = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task';
const LOCAL_BASE = '/static/vendor/mediapipe';

const SEND_HZ = 30;             // same throttle as the Quest client
const FINGERS = ['thumb', 'index', 'middle', 'ring', 'pinky'];
const STORE_SWAP = 'om.webcam_swap_sides';
const BACKGROUND_NOTE_MS = 10000;   // how long the "was paused" note stays after returning

// Bone list for the on-screen skeleton (tracker landmark indices).
const BONES = [
    [0, 1], [1, 2], [2, 3], [3, 4],
    [0, 5], [5, 6], [6, 7], [7, 8],
    [5, 9], [9, 10], [10, 11], [11, 12],
    [9, 13], [13, 14], [14, 15], [15, 16],
    [13, 17], [17, 18], [18, 19], [19, 20], [0, 17],
];

const EMBED = new URLSearchParams(location.search).get('embed') === '1';
document.body.classList.toggle('embed', EMBED);

const video = document.getElementById('video');
const overlay = document.getElementById('overlay');
const hint = document.getElementById('hint');
const cameraSelect = document.getElementById('camera');
const handSelect = document.getElementById('hand');
const swapBox = document.getElementById('swap');
const startBtn = document.getElementById('start');
const barsEl = document.getElementById('bars');
const statsEl = document.getElementById('stats');
const noticeEl = document.getElementById('notice');
const visWarnEl = document.getElementById('vis-warning');
const pillTracker = document.getElementById('pill-tracker');
const pillServer = document.getElementById('pill-server');
const pillHand = document.getElementById('pill-hand');

const CAMERA_PILL = {
    permission: 'camera: blocked', busy: 'camera: busy', nocamera: 'camera: not found',
    constraint: 'camera: not supported', insecure: 'camera: blocked',
    unsupported: 'camera: not supported',
};

const OS = detectOS(navigator.platform || '', navigator.userAgent || '');

let vision = null;          // the loaded tasks-vision module
let visionFiles = null;
let trackerSource = '';     // 'local' | 'cdn' (where the loaded tracker came from)
let tracker = null;
let failure = newFailureState('GPU');   // failure.delegate = delegate in use
let recovering = false;     // true while the tracker is being recreated on CPU
let stream = null;
let running = false;
let ws = null;
let wsEverOpened = false;
let wsFailures = 0;
let serverNotice = false;
let lastVideoTime = -1;
let lastSendAt = 0;
let framesSent = 0;
let detectTimes = [];
let sentIndexes = new Set();
let hiddenSince = null;
let backgroundNoteTimer = null;

const selector = new HandSelector({ swap: false, mode: 'auto' });

function setPill(el, text, cls) {
    el.textContent = text;
    el.className = 'pill' + (cls ? ' ' + cls : '');
    postState();
}

function setNotice(text) {
    noticeEl.textContent = text || '';
    noticeEl.style.display = text ? 'block' : 'none';
}

// Tell the dashboard (when embedded) what the camera is doing, so its panel
// header can show it while the panel is closed.
function postState() {
    if (!EMBED || window.parent === window) return;
    try {
        window.parent.postMessage({
            type: 'om-webcam-state',
            running,
            hand: pillHand.textContent,
            tracker: pillTracker.textContent,
            trackerClass: pillTracker.className,
        }, location.origin);
    } catch (e) { /* the dashboard is optional */ }
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
    ws.onopen = () => {
        wsEverOpened = true;
        wsFailures = 0;
        if (serverNotice) { serverNotice = false; setNotice(''); }
        setPill(pillServer, 'server: connected', 'ok');
    };
    ws.onclose = () => {
        if (!wsEverOpened && ++wsFailures >= 3) {
            // Never connected: the server turns away anything that is not this
            // computer (or a page from another site), so say what to do.
            setPill(pillServer, 'server: not connected', 'bad');
            serverNotice = true;
            setNotice('Cannot reach the OpenMuscle server. Check that OpenMuscle is still running '
                + 'and open this page on the computer that runs it, at '
                + `${cameraPageUrl(location)} .`);
        } else {
            setPill(pillServer, 'server: reconnecting', 'warn');
        }
        setTimeout(connect, 1500);
    };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* closing anyway */ } };
}

// ---------- tracker ----------

// Is a complete, checksum-verified local copy installed? The server checks
// every file (bundle, wasm, model), so "local" is never claimed for a partial
// copy that would fail halfway.
async function localTrackerReady() {
    try {
        const r = await fetch('/api/assets', { cache: 'no-store' });
        if (!r.ok) return false;
        const info = await r.json();
        return !!(info.groups && info.groups.tracker && info.groups.tracker.ready);
    } catch (e) {
        return false;
    }
}

function trackerOptions(model, delegate) {
    return {
        baseOptions: { modelAssetPath: model, delegate },
        runningMode: 'VIDEO',
        numHands: 2,
        minHandDetectionConfidence: 0.5,
        minHandPresenceConfidence: 0.5,
        minTrackingConfidence: 0.5,
    };
}

function sourceUrls(source) {
    return source === 'local'
        ? { base: LOCAL_BASE, model: `${LOCAL_BASE}/hand_landmarker.task` }
        : { base: CDN_BASE, model: CDN_MODEL };
}

// Create the tracker from one source. Tries the GPU first (unless `cpuOnly`)
// and drops to the CPU when graphics acceleration is unsupported. Sets
// failure.delegate to the delegate that actually came up.
async function createTracker(source, cpuOnly) {
    const { base, model } = sourceUrls(source);
    if (!vision || trackerSource !== source) {
        vision = await import(`${base}/vision_bundle.mjs`);
        visionFiles = await vision.FilesetResolver.forVisionTasks(`${base}/wasm`);
        trackerSource = source;
    }
    if (!cpuOnly) {
        try {
            tracker = await vision.HandLandmarker.createFromOptions(
                visionFiles, trackerOptions(model, 'GPU'));
            failure.delegate = 'GPU';
            return;
        } catch (e) { /* no usable graphics acceleration: use the CPU below */ }
    }
    tracker = await vision.HandLandmarker.createFromOptions(
        visionFiles, trackerOptions(model, 'CPU'));
    failure.delegate = 'CPU';
    setNotice(GPU_FALLBACK_NOTE);
}

async function loadTracker() {
    if (tracker) return;
    setPill(pillTracker, 'tracker: loading', 'warn');
    failure = newFailureState('GPU');
    const sources = (await localTrackerReady()) ? ['local', 'cdn'] : ['cdn'];
    let lastErr = null;
    for (const source of sources) {
        try {
            await createTracker(source, false);
            lastErr = null;
            break;
        } catch (e) {
            lastErr = e;
            vision = null;
            tracker = null;
        }
    }
    if (lastErr) throw lastErr;
    setPill(pillTracker, readyPillText(), 'ok');
}

// "local" = the copy on this computer; "internet" = downloaded for this run.
function readyPillText() {
    return `tracker: ready (${trackerSource === 'cdn' ? 'internet' : 'local'}`
        + (failure.delegate === 'CPU' ? ', CPU)' : ')');
}

function disposeTracker() {
    try { if (tracker && tracker.close) tracker.close(); } catch (e) { /* already gone */ }
    tracker = null;
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
    try {
        stream = await navigator.mediaDevices.getUserMedia({
            audio: false,
            video: {
                deviceId: id ? { exact: id } : undefined,
                width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30 },
            },
        });
    } catch (e) {
        if (e && e.name === 'OverconstrainedError' && id) {
            // The chosen camera is gone or cannot do this size: fall back to
            // the default camera rather than leaving the operator stuck.
            cameraSelect.value = '';
            stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: true });
        } else {
            throw e;
        }
    }
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

function drawOverlay(result) {
    const ctx = overlay.getContext('2d');
    const w = overlay.width;
    const h = overlay.height;
    ctx.clearRect(0, 0, w, h);
    (result.landmarks || []).forEach((lm, i) => {
        // The hand being sent is drawn bright and thick; any other hand in view
        // is drawn faint, so it is clear which one is recording.
        const sent = sentIndexes.has(i);
        ctx.strokeStyle = sent ? 'rgba(44, 209, 106, 0.95)' : 'rgba(138, 146, 163, 0.45)';
        ctx.lineWidth = sent ? 3 : 1.5;
        for (const [a, b] of BONES) {
            ctx.beginPath();
            ctx.moveTo(lm[a].x * w, lm[a].y * h);
            ctx.lineTo(lm[b].x * w, lm[b].y * h);
            ctx.stroke();
        }
        ctx.fillStyle = sent ? '#ffffff' : 'rgba(255, 255, 255, 0.4)';
        for (const p of lm) {
            ctx.beginPath();
            ctx.arc(p.x * w, p.y * h, sent ? 3 : 2, 0, Math.PI * 2);
            ctx.fill();
        }
    });
}

function frameMeta() {
    return {
        tracker: 'mediapipe-hand-landmarker',
        tracker_version: TRACKER_VERSION,
        tracker_assets: trackerSource === 'local' ? 'local' : 'cdn',
        delegate: failure.delegate,
        fps: detectTimes.length,
        swap_left_right: selector.swap,
        frame: 'hand_local',
        axes: 'origin wrist; y wrist to middle knuckle; x toward the index/thumb side; z = x cross y; meters',
        rotations: 'identity (not measured by a camera)',
        synthesized_joints: SYNTHESIZED_JOINTS,
        metacarpal_fraction: METACARPAL_FRACTION,
    };
}

// What the tracker found, in the plain form HandSelector wants.
function detections(result) {
    const handed = result.handedness || result.handednesses || [];
    return (result.worldLandmarks || []).map((_, i) => {
        const cat = (handed[i] && handed[i][0]) || {};
        const wrist = result.landmarks && result.landmarks[i] && result.landmarks[i][0];
        return { index: i, label: cat.categoryName, score: cat.score || 0,
                 wrist: wrist ? [wrist.x, wrist.y] : null };
    });
}

function onResult(result, nowMs) {
    const picked = selector.update(detections(result), nowMs).sends;
    sentIndexes = new Set(picked.map(p => p.index));
    drawOverlay(result);
    if (!picked.length) {
        setPill(pillHand, (result.worldLandmarks || []).length ? 'finding hand' : 'no hand',
                (result.worldLandmarks || []).length ? 'warn' : '');
        showFlexion(null);
        return;
    }
    const due = nowMs - lastSendAt >= 1000 / SEND_HZ;
    const open = ws && ws.readyState === WebSocket.OPEN;
    let shown = false;
    const labels = [];
    for (const p of picked) {
        const joints = toWebXRJoints(landmarksToArrays(result.worldLandmarks[p.index]));
        if (!joints) {            // hand edge-on or collapsed: skip this frame
            sentIndexes.delete(p.index);
            continue;
        }
        labels.push(p.side);
        if (!shown) { showFlexion(fingerFlexion(joints)); shown = true; }
        if (due && open) {
            ws.send(JSON.stringify({
                device_id: `webcam-${p.side}`,
                ts: Math.floor(nowMs),
                handedness: p.side,
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

// Something threw inside the tracking loop. Never let it end the loop silently:
// first failure on the GPU -> rebuild on the CPU and keep going; repeated
// failures -> stop and say so.
function onLoopError(e) {
    console.error('hand tracking failed', e);
    const action = onDetectError(failure);
    if (action === 'fallback-cpu') {
        recovering = true;
        setPill(pillTracker, 'tracker: switching to CPU', 'warn');
        disposeTracker();
        createTracker(trackerSource || 'cdn', true).then(() => {
            setPill(pillTracker, readyPillText(), 'ok');
            setNotice(GPU_FALLBACK_NOTE);
            lastVideoTime = -1;
        }).catch(err => {
            failRun(err);
        }).finally(() => { recovering = false; });
    } else if (action === 'stop') {
        failRun(e);
    }
}

function failRun(e) {
    const delegate = failure.delegate;
    stop();
    const d = describeError(e, { stage: 'run', os: OS, delegate, location });
    hint.style.display = 'flex';
    hint.textContent = d.text;
    setPill(pillTracker, 'tracker: stopped', 'bad');
    disposeTracker();    // the next Start builds a fresh one
    failure = newFailureState('GPU');
}

function loop() {
    if (!running) return;
    if (!recovering && tracker && video.readyState >= 2 && video.currentTime !== lastVideoTime) {
        try {
            lastVideoTime = video.currentTime;
            const now = performance.now();
            const result = tracker.detectForVideo(video, now);
            onDetectOk(failure);
            onResult(result, now);
            detectTimes.push(now);
            while (detectTimes.length && now - detectTimes[0] > 1000) detectTimes.shift();
            statsEl.textContent = `${detectTimes.length} frames/s tracked · ${framesSent} frames sent`;
        } catch (e) {
            onLoopError(e);
        }
    }
    if (running) requestAnimationFrame(loop);
}

// ---------- start / stop ----------

async function start() {
    startBtn.disabled = true;
    setNotice('');
    let stage = 'tracker';
    try {
        hint.style.display = 'flex';
        hint.textContent = 'Loading the hand tracker...';
        await loadTracker();
        stage = 'camera';
        hint.textContent = 'Waiting for the camera...';
        await startCamera();
        hint.style.display = 'none';
        running = true;
        selector.reset();
        lastVideoTime = -1;
        startBtn.textContent = 'Stop camera';
        postState();
        requestAnimationFrame(loop);
    } catch (e) {
        const d = describeError(e, {
            stage, os: OS, location, online: navigator.onLine,
            insecure: !window.isSecureContext,
        });
        hint.style.display = 'flex';
        hint.textContent = d.text;
        if (stage === 'tracker') {
            setPill(pillTracker, 'tracker: failed to load', 'bad');
            tracker = null;
        } else {
            setPill(pillHand, CAMERA_PILL[d.kind] || 'camera: problem', 'bad');
        }
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
    sentIndexes = new Set();
    detectTimes = [];
    setPill(pillHand, 'no hand', '');
    showFlexion(null);
    statsEl.textContent = 'not running';
}

startBtn.onclick = () => (running ? stop() : start());
cameraSelect.onchange = async () => {
    if (!running) return;
    stopCamera();
    try {
        await startCamera();
    } catch (e) {
        stop();
        hint.textContent = describeError(e, { stage: 'camera', os: OS, location }).text;
    }
};

// Which hand is in view / swap left and right.
handSelect.onchange = () => selector.setMode(handSelect.value);
try { swapBox.checked = localStorage.getItem(STORE_SWAP) === '1'; } catch (e) { /* storage blocked */ }
selector.setSwap(swapBox.checked);
swapBox.onchange = () => {
    selector.setSwap(swapBox.checked);
    try { localStorage.setItem(STORE_SWAP, swapBox.checked ? '1' : '0'); } catch (e) { /* lasts this load */ }
};

// ---------- window visibility ----------
//
// A browser stops drawing frames for a tab in the background, and tracking runs
// on drawn frames, so labels stop. Say so loudly while it is hidden and for a
// few seconds after coming back (the only time anyone can read it).
const PAGE_TITLE = document.title;

function showVisibilityNote(text) {
    visWarnEl.textContent = text;
    visWarnEl.style.display = text ? 'flex' : 'none';
}

document.addEventListener('visibilitychange', () => {
    clearTimeout(backgroundNoteTimer);
    document.title = PAGE_TITLE;
    if (document.hidden) {
        hiddenSince = performance.now();
        if (running) document.title = 'PAUSED: keep this window visible';
        showVisibilityNote(EMBED
            ? 'Keep this tab visible: hand tracking pauses while the tab is in the background.'
            : 'Keep this window visible: labels pause when this tab is in the background. '
              + 'Put it side by side with the dashboard, not behind it.');
        if (running) setPill(pillHand, 'paused: window hidden', 'warn');
        return;
    }
    if (hiddenSince != null && running) {
        const secs = Math.round((performance.now() - hiddenSince) / 1000);
        showVisibilityNote(`Tracking was paused for ${secs} s while this window was in the `
            + 'background, so no hand labels were sent in that time. Keep this window visible '
            + (EMBED ? 'while you record.' : 'next to the dashboard (side by side, not behind it).'));
        backgroundNoteTimer = setTimeout(() => showVisibilityNote(''), BACKGROUND_NOTE_MS);
    } else {
        showVisibilityNote('');
    }
    hiddenSince = null;
    if (running) setPill(pillHand, 'no hand', '');
});

// ---------- boot ----------

if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    // Browsers hide the camera API entirely on an address that is not
    // http://localhost or https, which is the usual cause.
    hint.textContent = window.isSecureContext
        ? unsupportedText(cameraPageUrl(location))
        : describeError({ name: 'SecurityError' }, { stage: 'camera', os: OS, location }).text;
    startBtn.disabled = true;
}
if (typeof WebAssembly !== 'object') {
    hint.textContent = unsupportedText(cameraPageUrl(location));
    startBtn.disabled = true;
}

// For checking the failure paths from the browser console, e.g.
// omWebcamDebug.tracker().detectForVideo = () => { throw new Error('x'); }
window.omWebcamDebug = {
    tracker: () => tracker,
    delegate: () => failure.delegate,
    selector,
};

showFlexion(null);
setNotice('');
connect();
listCameras();

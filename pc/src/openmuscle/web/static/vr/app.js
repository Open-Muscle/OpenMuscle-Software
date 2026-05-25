// OpenMuscle VR client. Runs in Quest Browser via WebXR.
//
// Two WebSockets to the FastAPI server on the same host:
//   /ws/quest  outbound: per-XRFrame XRHand joints (the new "label" stream)
//   /ws/live   inbound:  device snapshots (used to paint the heatmap panel)
//
// Why this works without any new transport: every Quest frame we ingest
// is synthesized server-side into an OpenMusclePacket(device_type="quest_hand")
// and routed through the same matcher/recorder path as UDP devices. From the
// server's view the Quest is just another label source.

import * as THREE from 'three';
import { VRButton } from 'three/addons/webxr/VRButton.js';

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

// OpenXR / WebXR Hand Input standard joint set, in canonical order. We send
// these in the same order every frame so server-side label_i columns map
// stably to (joint, channel). The labels-schema sidecar emits this exact list.
const JOINT_NAMES = [
    'wrist',
    'thumb-metacarpal',         'thumb-phalanx-proximal',
    'thumb-phalanx-distal',     'thumb-tip',
    'index-finger-metacarpal',  'index-finger-phalanx-proximal',
    'index-finger-phalanx-intermediate', 'index-finger-phalanx-distal',
    'index-finger-tip',
    'middle-finger-metacarpal', 'middle-finger-phalanx-proximal',
    'middle-finger-phalanx-intermediate', 'middle-finger-phalanx-distal',
    'middle-finger-tip',
    'ring-finger-metacarpal',   'ring-finger-phalanx-proximal',
    'ring-finger-phalanx-intermediate', 'ring-finger-phalanx-distal',
    'ring-finger-tip',
    'pinky-finger-metacarpal',  'pinky-finger-phalanx-proximal',
    'pinky-finger-phalanx-intermediate', 'pinky-finger-phalanx-distal',
    'pinky-finger-tip',
];

const params = new URLSearchParams(location.search);
const ARM = params.get('arm') === 'left' ? 'left' : 'right';   // FlexGrid-arm side
const PINCH_THRESHOLD_M  = 0.025;   // 2.5 cm index-tip <-> thumb-tip
const PINCH_HOLD_MS      = 1000;    // hold this long to toggle recording
const BUTTON_TOUCH_M     = 0.04;    // 4 cm finger-tip proximity = "pressing"
const HEATMAP_FORWARD_M  = 0.70;    // panel placed 70cm in front at session start
const HEATMAP_W          = 0.40;    // 40cm panel matches FlexGrid 15:4 aspect
const HEATMAP_H          = 0.12;
const BUTTON_RADIUS_M    = 0.04;
const BUTTON_ROW_DOWN    = 0.20;    // button row sits 20cm below the heatmap center
const BUTTON_SPACING_M   = 0.13;    // horizontal spacing between buttons
const STATUS_ROW_DOWN    = 0.34;    // status strip sits 34cm below the heatmap
const STATUS_W           = HEATMAP_W;
const STATUS_H           = 0.045;
const STATUS_FADE_MS     = 6000;    // status text fully fades after 6s of no update
const REPORT_HZ          = 30;      // throttle /ws/quest sends (~30Hz is plenty)
const BUTTON_COOLDOWN_MS = 800;     // min gap between two activations of the same button

// ---------------------------------------------------------------------------
// Landing-page checks (run before VR session starts)
// ---------------------------------------------------------------------------

function setCheck(id, state, text) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('ok', 'bad', 'pending');
    el.classList.add(state);
    if (text) el.textContent = text;
}

function isSecureContext() {
    // WebXR refuses hand-tracking outside a secure context. localhost counts.
    return window.isSecureContext === true;
}

async function preflightChecks() {
    setCheck('check-https', isSecureContext() ? 'ok' : 'bad',
             isSecureContext() ? `secure context (${location.protocol})`
                                : `INSECURE (${location.protocol}) -- need HTTPS or localhost`);
    let xrOK = false;
    if (navigator.xr) {
        try {
            xrOK = await navigator.xr.isSessionSupported('immersive-vr');
        } catch (e) { xrOK = false; }
    }
    setCheck('check-xr', xrOK ? 'ok' : 'bad',
             xrOK ? 'WebXR immersive-vr supported'
                  : 'WebXR not available (open this URL in Quest Browser)');
    try {
        const r = await fetch('/api/devices');
        setCheck('check-server', r.ok ? 'ok' : 'bad',
                 r.ok ? `server reachable (${r.status})`
                      : `server returned ${r.status}`);
    } catch (e) {
        setCheck('check-server', 'bad', `server unreachable (${e.message})`);
    }
    document.getElementById('arm-select').value = ARM;
    document.getElementById('arm-select').addEventListener('change', (e) => {
        // Re-load with the new arm in the URL so the choice survives session start.
        const u = new URL(location.href);
        u.searchParams.set('arm', e.target.value);
        location.href = u.toString();
    });
}

// ---------------------------------------------------------------------------
// Scene + XR
// ---------------------------------------------------------------------------

let scene, camera, renderer;
let heatmapMesh, heatmapCanvas, heatmapCtx, heatmapTex;
let headerCanvas, headerTex, headerMesh;
let statusMesh, statusCanvas, statusTex;
let pinchIndicator;
let armGroup;                                  // holds joint visualizer spheres
let armJointMeshes = new Map();                // joint-name -> sphere mesh
let placed = false;                            // anchors set on first XRFrame

// Three labeled buttons in a row: REC / TRAIN / SESSION. Each entry holds
// its mesh group, material refs for color updates, hover state (to debounce
// touch-enter from touch-stay), and an `onActivate` callback.
const buttons = {};   // name -> { group, base, label, labelCanvas, labelCtx, labelTex,
                      //            hover, lastActivateAt, isActive: () => bool,
                      //            onActivate: () => void, color: int }

// Server-derived state (mirrored into button visuals each frame from /ws/live)
const uiState = {
    recording: false,
    sessionActive: false,
    sessionId: null,
    training: false,
    // pinch is the hands-free fallback for REC only
    pinchStart: 0,
    lastPinchToggleAt: 0,
};

let lastReportAt = 0;
let lastStatusAt = 0;
let lastStatusText = '';

function initScene() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x05060a);

    camera = new THREE.PerspectiveCamera(70, window.innerWidth / window.innerHeight,
                                          0.05, 50);
    camera.position.set(0, 1.6, 0);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.xr.enabled = true;
    document.body.appendChild(renderer.domElement);
    renderer.domElement.style.display = 'none';   // hidden while landing page is up

    scene.add(new THREE.HemisphereLight(0xa0c4ff, 0x202028, 0.9));
    const dir = new THREE.DirectionalLight(0xffffff, 0.6);
    dir.position.set(1, 2, 0.5);
    scene.add(dir);

    // Heatmap panel (PlaneGeometry + CanvasTexture, painted from /ws/live)
    heatmapCanvas = document.createElement('canvas');
    heatmapCanvas.width = 600; heatmapCanvas.height = 180;
    heatmapCtx = heatmapCanvas.getContext('2d');
    drawHeatmapPlaceholder();
    heatmapTex = new THREE.CanvasTexture(heatmapCanvas);
    heatmapTex.colorSpace = THREE.SRGBColorSpace;
    const heatMat = new THREE.MeshBasicMaterial({ map: heatmapTex,
                                                   side: THREE.DoubleSide });
    heatmapMesh = new THREE.Mesh(new THREE.PlaneGeometry(HEATMAP_W, HEATMAP_H),
                                  heatMat);
    scene.add(heatmapMesh);

    // Header strip above the heatmap (status text rendered on its own canvas)
    headerCanvas = document.createElement('canvas');
    headerCanvas.width = 600; headerCanvas.height = 90;
    headerTex = new THREE.CanvasTexture(headerCanvas);
    headerTex.colorSpace = THREE.SRGBColorSpace;
    headerMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(HEATMAP_W, HEATMAP_H * 0.5),
        new THREE.MeshBasicMaterial({ map: headerTex, transparent: true }));
    scene.add(headerMesh);
    drawHeader('connecting…', false);

    // Button row: REC / TRAIN / SESSION. Each button is a small sphere with
    // a text-labeled plane just above it. Activation = off-hand index-tip
    // proximity (BUTTON_TOUCH_M). The pinch-to-record gesture remains as a
    // hands-free alternative for REC only.
    createButton('REC',
                 () => uiState.recording,
                 toggleRecording);
    createButton('TRAIN',
                 () => uiState.training,
                 runTrain);
    createButton('SESSION',
                 () => uiState.sessionActive,
                 toggleSession);

    // Status strip below the buttons -- canvas-textured plane that shows
    // action feedback ("training...", "trained: R²=0.81", "session started").
    statusCanvas = document.createElement('canvas');
    statusCanvas.width = 800; statusCanvas.height = 90;
    statusTex = new THREE.CanvasTexture(statusCanvas);
    statusTex.colorSpace = THREE.SRGBColorSpace;
    statusMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(STATUS_W, STATUS_H),
        new THREE.MeshBasicMaterial({ map: statusTex, transparent: true }));
    scene.add(statusMesh);
    drawStatus('');

    // Pinch progress ring (drawn on a small canvas, pinned to the captured hand's
    // wrist each frame; opacity scales with pinch hold time)
    pinchIndicator = new THREE.Mesh(
        new THREE.RingGeometry(0.025, 0.030, 24, 1, 0, Math.PI * 2),
        new THREE.MeshBasicMaterial({ color: 0xfbbf24, transparent: true,
                                       opacity: 0, side: THREE.DoubleSide }));
    pinchIndicator.visible = false;
    scene.add(pinchIndicator);

    // Group for the joint visualizer spheres (one per captured joint)
    armGroup = new THREE.Group();
    scene.add(armGroup);
    const jointGeo = new THREE.SphereGeometry(0.006, 8, 6);
    const jointMat = new THREE.MeshBasicMaterial({ color: 0x60a5fa });
    for (const name of JOINT_NAMES) {
        const m = new THREE.Mesh(jointGeo, jointMat);
        m.visible = false;
        armGroup.add(m);
        armJointMeshes.set(name, m);
    }

    window.addEventListener('resize', () => {
        if (renderer.xr.isPresenting) return;  // XR owns the projection then
        camera.aspect = window.innerWidth / window.innerHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(window.innerWidth, window.innerHeight);
    });
}

function createButton(name, isActive, onActivate) {
    // Body: small sphere. Color updated each frame from button state.
    const baseMat = new THREE.MeshStandardMaterial({
        color: 0x4d5566, metalness: 0.2, roughness: 0.6,
        emissive: 0x000000, emissiveIntensity: 0.0,
    });
    const base = new THREE.Mesh(
        new THREE.SphereGeometry(BUTTON_RADIUS_M, 24, 16), baseMat);

    // Label: text on a canvas, projected onto a small plane just above the
    // body. Kept billboard-style (lookAt camera each frame in updateButtonVisual)
    // so the text is always readable from the user's POV.
    const labelCanvas = document.createElement('canvas');
    labelCanvas.width = 256; labelCanvas.height = 96;
    const labelCtx = labelCanvas.getContext('2d');
    const labelTex = new THREE.CanvasTexture(labelCanvas);
    labelTex.colorSpace = THREE.SRGBColorSpace;
    const label = new THREE.Mesh(
        new THREE.PlaneGeometry(BUTTON_RADIUS_M * 2.6, BUTTON_RADIUS_M * 1.0),
        new THREE.MeshBasicMaterial({ map: labelTex, transparent: true,
                                       depthWrite: false }));
    label.position.set(0, BUTTON_RADIUS_M * 1.6, 0);

    const group = new THREE.Group();
    group.add(base);
    group.add(label);
    scene.add(group);

    buttons[name] = {
        group, base, baseMat, label, labelCanvas, labelCtx, labelTex,
        name, hover: false, lastActivateAt: 0,
        isActive, onActivate,
    };
    drawButtonLabel(buttons[name], name, false);
}

function drawButtonLabel(btn, text, isActive) {
    const ctx = btn.labelCtx;
    const W = btn.labelCanvas.width, H = btn.labelCanvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = isActive ? 'rgba(239, 68, 68, 0.85)'
                              : 'rgba(20, 24, 32, 0.75)';
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#f0f4f8';
    ctx.font = 'bold 48px system-ui';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, W / 2, H / 2);
    btn.labelTex.needsUpdate = true;
}

function drawStatus(text) {
    if (text !== lastStatusText) {
        lastStatusText = text;
        lastStatusAt = performance.now();
    }
    const ctx = statusCanvas.getContext('2d');
    const W = statusCanvas.width, H = statusCanvas.height;
    ctx.clearRect(0, 0, W, H);
    if (!text) { statusTex.needsUpdate = true; return; }
    // Fade alpha over STATUS_FADE_MS
    const age = performance.now() - lastStatusAt;
    const alpha = Math.max(0, 1 - age / STATUS_FADE_MS);
    if (alpha <= 0) { statusTex.needsUpdate = true; return; }
    ctx.globalAlpha = alpha;
    ctx.fillStyle = 'rgba(20, 24, 32, 0.80)';
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#d8dde6';
    ctx.font = '30px system-ui';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, W / 2, H / 2);
    ctx.globalAlpha = 1;
    statusTex.needsUpdate = true;
}

function setStatus(text) {
    // Update text + restart fade timer
    lastStatusText = text;
    lastStatusAt = performance.now();
    drawStatus(text);
}

function placeAnchors(frame, refSpace) {
    // Use the headset's current pose to place panel + button in front of the
    // user at session start. After this they're world-anchored -- they don't
    // follow the head (head-locked panels make people queasy).
    const viewerPose = frame.getViewerPose(refSpace);
    if (!viewerPose) return;
    const view = viewerPose.views[0];
    const m = new THREE.Matrix4().fromArray(view.transform.matrix);
    const headPos = new THREE.Vector3().setFromMatrixPosition(m);
    const headFwd = new THREE.Vector3(0, 0, -1).applyMatrix4(
        new THREE.Matrix4().extractRotation(m));

    // Place heatmap 70cm in front, slightly below eye height
    const heatPos = headPos.clone().addScaledVector(headFwd, HEATMAP_FORWARD_M);
    heatPos.y -= 0.10;
    heatmapMesh.position.copy(heatPos);
    heatmapMesh.lookAt(headPos);

    // Header centered above the heatmap
    headerMesh.position.copy(heatPos).add(new THREE.Vector3(0, HEATMAP_H * 0.6, 0));
    headerMesh.lookAt(headPos);

    // Button row sits below the heatmap. We lay the three buttons out along
    // the panel's local-right axis (perpendicular to head-forward + world up)
    // so they stay parallel to the heatmap even if the user wasn't looking
    // straight down +Z at session start.
    const worldUp = new THREE.Vector3(0, 1, 0);
    const panelRight = new THREE.Vector3().crossVectors(worldUp, headFwd).normalize();
    const rowCenter = heatPos.clone().add(new THREE.Vector3(0, -BUTTON_ROW_DOWN, 0));
    const order = ['REC', 'TRAIN', 'SESSION'];
    for (let i = 0; i < order.length; i++) {
        const offset = (i - (order.length - 1) / 2) * BUTTON_SPACING_M;
        const pos = rowCenter.clone().addScaledVector(panelRight, offset);
        const btn = buttons[order[i]];
        if (!btn) continue;
        btn.group.position.copy(pos);
        // Label plane faces the user (lookAt updated per-frame in
        // updateButtonVisual so it stays readable as the user moves)
    }

    // Status strip below the buttons
    statusMesh.position.copy(heatPos).add(new THREE.Vector3(0, -STATUS_ROW_DOWN, 0));
    statusMesh.lookAt(headPos);

    placed = true;
}

// ---------------------------------------------------------------------------
// WebSockets
// ---------------------------------------------------------------------------

let questWs = null;
let liveWs = null;
let latestSnapshot = null;

function wsURL(path) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}${path}`;
}

function connectQuestWS() {
    questWs = new WebSocket(wsURL('/ws/quest'));
    questWs.addEventListener('close', () => {
        // Reconnect with backoff so a network blip doesn't lose the session
        setTimeout(connectQuestWS, 1500);
    });
    questWs.addEventListener('error', () => { try { questWs.close(); } catch (e) {} });
}

function connectLiveWS() {
    liveWs = new WebSocket(wsURL('/ws/live'));
    liveWs.addEventListener('message', (ev) => {
        try { latestSnapshot = JSON.parse(ev.data); } catch (e) {}
    });
    liveWs.addEventListener('close', () => setTimeout(connectLiveWS, 1500));
    liveWs.addEventListener('error', () => { try { liveWs.close(); } catch (e) {} });
}

// ---------------------------------------------------------------------------
// Per-frame: capture hand, detect pinch, raycast button, paint heatmap
// ---------------------------------------------------------------------------

function jointPose(frame, refSpace, hand, name) {
    const j = hand.get(name);
    if (!j) return null;
    return frame.getJointPose(j, refSpace);
}

function captureAndSend(frame, refSpace, hand, timestampMs) {
    if (!questWs || questWs.readyState !== WebSocket.OPEN) return;
    if (timestampMs - lastReportAt < 1000 / REPORT_HZ) return;
    lastReportAt = timestampMs;

    const joints = [];
    for (const name of JOINT_NAMES) {
        const p = jointPose(frame, refSpace, hand, name);
        if (!p) continue;
        const t = p.transform;
        joints.push({
            name,
            pos: [t.position.x, t.position.y, t.position.z],
            rot: [t.orientation.x, t.orientation.y, t.orientation.z, t.orientation.w],
            radius: p.radius || 0,
        });
    }
    if (joints.length === 0) return;  // tracking lost; let it drop
    questWs.send(JSON.stringify({
        device_id: `quest-${ARM}`,
        ts: Math.floor(timestampMs),
        handedness: ARM,
        joints,
    }));
}

function updateArmVisualizer(frame, refSpace, hand) {
    for (const [name, mesh] of armJointMeshes) {
        const p = jointPose(frame, refSpace, hand, name);
        if (!p) { mesh.visible = false; continue; }
        mesh.visible = true;
        mesh.position.set(p.transform.position.x,
                          p.transform.position.y,
                          p.transform.position.z);
    }
}

function detectPinchAndToggle(frame, refSpace, hand, timestampMs) {
    const ip = jointPose(frame, refSpace, hand, 'index-finger-tip');
    const tp = jointPose(frame, refSpace, hand, 'thumb-tip');
    if (!ip || !tp) { uiState.pinchStart = 0; pinchIndicator.visible = false; return; }
    const dx = ip.transform.position.x - tp.transform.position.x;
    const dy = ip.transform.position.y - tp.transform.position.y;
    const dz = ip.transform.position.z - tp.transform.position.z;
    const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
    const isPinching = dist < PINCH_THRESHOLD_M;

    // Park the pinch indicator on the index tip and orient toward the head
    pinchIndicator.position.set(ip.transform.position.x,
                                ip.transform.position.y,
                                ip.transform.position.z);
    const head = renderer.xr.getCamera ? renderer.xr.getCamera() : camera;
    pinchIndicator.lookAt(head.position);

    if (isPinching) {
        if (uiState.pinchStart === 0) uiState.pinchStart = timestampMs;
        const held = timestampMs - uiState.pinchStart;
        const progress = Math.min(1.0, held / PINCH_HOLD_MS);
        pinchIndicator.visible = true;
        pinchIndicator.material.opacity = 0.3 + 0.7 * progress;
        if (held >= PINCH_HOLD_MS && timestampMs - uiState.lastPinchToggleAt > 1500) {
            toggleRecording();
            uiState.lastPinchToggleAt = timestampMs;
            uiState.pinchStart = -Infinity;  // require release before next
        }
    } else {
        uiState.pinchStart = 0;
        pinchIndicator.visible = false;
    }
}

function checkButtonTouchByOffHand(frame, refSpace, session, timestampMs) {
    // The OFF hand (not the captured one) is what taps the buttons. Pressing
    // a button with the captured hand would smear the gesture you're trying
    // to record. We iterate every off-hand input source -- on Quest 3S there's
    // only one of each handedness so it's effectively a single check.
    const offHands = [];
    for (const input of session.inputSources) {
        if (input.hand && input.handedness !== ARM) offHands.push(input.hand);
    }
    if (offHands.length === 0) {
        // Tracking lost on the off-hand: reset all hovers so a re-touch
        // re-triggers cleanly rather than being eaten by stale hover state.
        for (const btn of Object.values(buttons)) btn.hover = false;
        return;
    }

    for (const btn of Object.values(buttons)) {
        let nearest = Infinity;
        for (const hand of offHands) {
            const tip = jointPose(frame, refSpace, hand, 'index-finger-tip');
            if (!tip) continue;
            const dx = tip.transform.position.x - btn.group.position.x;
            const dy = tip.transform.position.y - btn.group.position.y;
            const dz = tip.transform.position.z - btn.group.position.z;
            const d = Math.sqrt(dx * dx + dy * dy + dz * dz);
            if (d < nearest) nearest = d;
        }
        const insideR = BUTTON_RADIUS_M + BUTTON_TOUCH_M;
        const releaseR = insideR + 0.02;
        if (nearest < insideR && !btn.hover
                && timestampMs - btn.lastActivateAt > BUTTON_COOLDOWN_MS) {
            btn.lastActivateAt = timestampMs;
            btn.hover = true;
            try { btn.onActivate(); }
            catch (e) { console.error(`button ${btn.name} activate failed:`, e); }
        } else if (nearest > releaseR) {
            btn.hover = false;
        }
    }
}

function updateButtonVisual() {
    // Idle vs active colors per button. Active = the underlying state the
    // button represents is "on" (e.g. recording in progress for REC, session
    // open for SESSION, training in flight for TRAIN).
    const head = renderer.xr.getCamera ? renderer.xr.getCamera() : camera;
    for (const btn of Object.values(buttons)) {
        const active = !!btn.isActive();
        if (active) {
            btn.baseMat.color.setHex(0xef4444);
            btn.baseMat.emissive.setHex(0xef4444);
            btn.baseMat.emissiveIntensity = 0.55;
        } else if (btn.hover) {
            btn.baseMat.color.setHex(0x7a8499);
            btn.baseMat.emissive.setHex(0x1f6feb);
            btn.baseMat.emissiveIntensity = 0.25;
        } else {
            btn.baseMat.color.setHex(0x4d5566);
            btn.baseMat.emissive.setHex(0x000000);
            btn.baseMat.emissiveIntensity = 0.0;
        }
        // Label text changes for REC (REC <-> STOP) so the user knows
        // what tapping it again will do. TRAIN and SESSION keep their
        // label but the body color signals state.
        const labelText = (btn.name === 'REC' && active) ? 'STOP' : btn.name;
        drawButtonLabel(btn, labelText, active);
        // Billboard the label toward the user's head so it's readable.
        btn.label.lookAt(head.position);
    }
}

// ---------------------------------------------------------------------------
// Heatmap rendering: paint /ws/live's flexgrid matrix onto our canvas
// ---------------------------------------------------------------------------

function drawHeatmapPlaceholder() {
    heatmapCtx.fillStyle = '#0d1117';
    heatmapCtx.fillRect(0, 0, heatmapCanvas.width, heatmapCanvas.height);
    heatmapCtx.fillStyle = '#586069';
    heatmapCtx.font = '20px system-ui';
    heatmapCtx.textAlign = 'center';
    heatmapCtx.fillText('waiting for FlexGrid…',
                         heatmapCanvas.width / 2, heatmapCanvas.height / 2 + 7);
}

function colorRamp(t) {
    // black -> purple -> pink -> orange -> yellow (same family the desktop
    // web UI uses, but recomputed here so we don't depend on its CSS)
    t = Math.max(0, Math.min(1, t));
    const stops = [
        [0.00,  10,  12,  22],
        [0.25,  78,  19,  98],
        [0.50, 188,  37, 122],
        [0.75, 244, 122,  46],
        [1.00, 252, 232, 132],
    ];
    for (let i = 0; i < stops.length - 1; i++) {
        const [t0, r0, g0, b0] = stops[i];
        const [t1, r1, g1, b1] = stops[i + 1];
        if (t <= t1) {
            const k = (t - t0) / (t1 - t0);
            return [r0 + (r1 - r0) * k, g0 + (g1 - g0) * k, b0 + (b1 - b0) * k];
        }
    }
    return [252, 232, 132];
}

let vmaxObserved = 100;
let lastDrawnMatrixSig = '';

function drawHeatmap(matrix) {
    if (!matrix || matrix.length === 0) return;
    const cols = matrix.length;
    const rows = matrix[0].length;
    // Compute vmax (auto-scale upward only, so quick gestures don't dim the panel)
    let m = 1;
    for (let c = 0; c < cols; c++)
        for (let r = 0; r < rows; r++)
            if (matrix[c][r] > m) m = matrix[c][r];
    if (m > vmaxObserved) vmaxObserved = m;
    const vmax = vmaxObserved;

    // Skip re-paint if matrix is unchanged (cheap signature check)
    const sig = matrix[0][0] + ':' + matrix[cols - 1][rows - 1] + ':' + vmax;
    if (sig === lastDrawnMatrixSig) return;
    lastDrawnMatrixSig = sig;

    const W = heatmapCanvas.width, H = heatmapCanvas.height;
    heatmapCtx.fillStyle = '#0d1117';
    heatmapCtx.fillRect(0, 0, W, H);
    const cw = W / cols, ch = H / rows;
    for (let c = 0; c < cols; c++) {
        for (let r = 0; r < rows; r++) {
            const v = matrix[c][r] / vmax;
            const [R, G, B] = colorRamp(v);
            heatmapCtx.fillStyle = `rgb(${R | 0}, ${G | 0}, ${B | 0})`;
            heatmapCtx.fillRect(c * cw, r * ch, Math.ceil(cw), Math.ceil(ch));
        }
    }
    heatmapTex.needsUpdate = true;
}

function drawHeader(text, isRecording) {
    const ctx = headerCanvas.getContext('2d');
    ctx.clearRect(0, 0, headerCanvas.width, headerCanvas.height);
    ctx.fillStyle = isRecording ? 'rgba(239, 68, 68, 0.95)' : 'rgba(20, 24, 32, 0.85)';
    const pad = 12;
    ctx.fillRect(pad, pad, headerCanvas.width - pad * 2, headerCanvas.height - pad * 2);
    ctx.fillStyle = '#f0f4f8';
    ctx.font = 'bold 32px system-ui';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    ctx.fillText(text, headerCanvas.width / 2, headerCanvas.height / 2);
    headerTex.needsUpdate = true;
}

function updateFromSnapshot(snap, timestampMs) {
    if (!snap) return;
    // Pick the first flexgrid device's matrix to paint
    const fg = (snap.devices || []).find(d => d.device_type === 'flexgrid' && d.matrix?.length);
    if (fg) drawHeatmap(fg.matrix);
    // Header text: device count + recording status
    const rec = snap.recording;
    if (rec) {
        drawHeader(`REC · ${rec.rows} rows · match ${(rec.match_rate * 100).toFixed(0)}%`, true);
        uiState.recording = true;
    } else {
        const fgHz = fg ? `${fg.hz?.toFixed?.(0) || '0'} Hz` : 'no FlexGrid';
        const questDev = (snap.devices || []).find(d => d.device_type === 'quest_hand');
        const questHz = questDev ? `${questDev.hz?.toFixed?.(0) || '0'} Hz` : 'no Quest';
        drawHeader(`${fgHz} · ${questHz}`, false);
        uiState.recording = false;
    }
    // Session state mirrors the server's active_session field
    uiState.sessionActive = !!snap.active_session;
    uiState.sessionId = snap.active_session?.id || null;
    // Re-render the status strip every frame so its fade animates without
    // needing a separate timer.
    drawStatus(lastStatusText);
}

// ---------------------------------------------------------------------------
// Button actions: each REST call surfaces success/failure in the status strip
// so the user doesn't have to take the headset off to know what happened.
// ---------------------------------------------------------------------------

async function toggleRecording() {
    try {
        if (uiState.recording) {
            const r = await fetch('/api/recording', { method: 'DELETE' });
            if (r.ok) {
                const data = await r.json();
                setStatus(`saved: ${data.filename} (${data.rows} rows, match ${(data.match_rate * 100).toFixed(0)}%)`);
            } else {
                setStatus(`stop failed: HTTP ${r.status}`);
            }
        } else {
            const r = await fetch('/api/recording', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),     // server picks Quest as label + window=175
            });
            if (r.ok) {
                const data = await r.json();
                setStatus(`recording: ${data.filename} (window ${data.window_ms}ms)`);
            } else {
                const err = await r.text();
                setStatus(`start failed: ${err.slice(0, 60)}`);
            }
        }
    } catch (e) {
        setStatus(`record toggle failed: ${e.message}`);
    }
}

async function toggleSession() {
    try {
        if (uiState.sessionActive) {
            const r = await fetch('/api/sessions/end', { method: 'POST' });
            if (r.ok) {
                const s = await r.json();
                setStatus(`session ended: ${s.id} (${s.capture_count} captures)`);
            } else {
                setStatus(`end session failed: HTTP ${r.status}`);
            }
        } else {
            const r = await fetch('/api/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: 'vr-' + new Date().toISOString().slice(0, 19) }),
            });
            if (r.ok) {
                const s = await r.json();
                setStatus(`session started: ${s.id}`);
            } else {
                const err = await r.text();
                setStatus(`start session failed: ${err.slice(0, 60)}`);
            }
        }
    } catch (e) {
        setStatus(`session toggle failed: ${e.message}`);
    }
}

async function runTrain() {
    if (uiState.training) {
        setStatus('training already in flight — wait for it to finish');
        return;
    }
    if (uiState.recording) {
        setStatus('stop recording before training');
        return;
    }
    // Pick captures to train on: active session's captures if any, else
    // fall back to the most recent capture only. The fallback keeps the
    // "tap TRAIN right after a recording" flow alive even without sessions.
    let captureNames = [];
    try {
        if (uiState.sessionActive) {
            const r = await fetch(`/api/sessions/${uiState.sessionId}`);
            if (r.ok) {
                const s = await r.json();
                captureNames = s.captures || [];
            }
        }
        if (captureNames.length === 0) {
            const r = await fetch('/api/captures');
            if (r.ok) {
                const caps = await r.json();
                if (caps.length > 0) captureNames = [caps[0].name];
            }
        }
        if (captureNames.length === 0) {
            setStatus('no captures to train on -- record something first');
            return;
        }

        uiState.training = true;
        setStatus(`training on ${captureNames.length} capture(s)…`);
        const r = await fetch('/api/train', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ captures: captureNames, activate: true }),
        });
        if (r.ok) {
            const result = await r.json();
            const r2 = result.metrics?.r2;
            const r2str = (typeof r2 === 'number') ? r2.toFixed(3) : '?';
            setStatus(`trained: R²=${r2str}` + (result.active ? ' · model loaded ✓' : ' (saved only)'));
        } else {
            const err = await r.text();
            setStatus(`train failed: ${err.slice(0, 80)}`);
        }
    } catch (e) {
        setStatus(`train error: ${e.message}`);
    } finally {
        uiState.training = false;
    }
}

// ---------------------------------------------------------------------------
// Main XR frame loop
// ---------------------------------------------------------------------------

function onXRFrame(timestamp, frame) {
    const session = renderer.xr.getSession();
    const refSpace = renderer.xr.getReferenceSpace();
    if (!session || !refSpace) return;

    if (!placed) placeAnchors(frame, refSpace);

    let capturedHand = null;
    for (const input of session.inputSources) {
        if (input.hand && input.handedness === ARM) { capturedHand = input.hand; break; }
    }
    if (capturedHand) {
        captureAndSend(frame, refSpace, capturedHand, timestamp);
        updateArmVisualizer(frame, refSpace, capturedHand);
        detectPinchAndToggle(frame, refSpace, capturedHand, timestamp);
    } else {
        // Hide the joint spheres + pinch ring if the captured hand isn't tracked
        for (const m of armJointMeshes.values()) m.visible = false;
        pinchIndicator.visible = false;
    }

    checkButtonTouchByOffHand(frame, refSpace, session, timestamp);
    updateButtonVisual();
    updateFromSnapshot(latestSnapshot, timestamp);

    renderer.render(scene, camera);
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

function bootVRButton() {
    initScene();
    const button = VRButton.createButton(renderer, {
        requiredFeatures: ['local-floor'],
        optionalFeatures: ['hand-tracking'],
    });
    document.getElementById('enter-vr-mount').appendChild(button);

    renderer.xr.addEventListener('sessionstart', () => {
        document.getElementById('landing').style.display = 'none';
        renderer.domElement.style.display = 'block';
        placed = false;
        connectQuestWS();
        connectLiveWS();
        renderer.setAnimationLoop(onXRFrame);
    });
    renderer.xr.addEventListener('sessionend', () => {
        renderer.setAnimationLoop(null);
        document.getElementById('landing').style.display = '';
        renderer.domElement.style.display = 'none';
        try { questWs && questWs.close(); } catch (e) {}
        try { liveWs && liveWs.close(); } catch (e) {}
        questWs = null; liveWs = null;
    });
}

(async function main() {
    await preflightChecks();
    bootVRButton();
})();

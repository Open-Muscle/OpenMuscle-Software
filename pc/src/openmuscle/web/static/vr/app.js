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
import { ARButton } from 'three/addons/webxr/ARButton.js';

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
// MODE: 'vr' (fully immersive black background, the v1.x default — used for
// deliberate gesture training in a controlled space) vs 'ar' (passthrough
// background, real world visible behind our panels — used for field-capture
// sessions during real activities). See docs/vr-setup.md for the use cases.
const MODE = params.get('mode') === 'ar' ? 'ar' : 'vr';
const XR_SESSION_TYPE = MODE === 'ar' ? 'immersive-ar' : 'immersive-vr';
const PINCH_THRESHOLD_M  = 0.025;   // 2.5 cm index-tip <-> thumb-tip
const PINCH_HOLD_MS      = 1000;    // hold this long to toggle recording (captured arm)

// AR mode pushes panels further away + shrinks them so they don't dominate
// your view of the real world. The same panel sizes that read as
// "comfortably in front of me" in VR (no real-world reference) feel HUGE in
// passthrough against an actual kitchen/desk/workshop. Default VR sizes are
// the v1.x constants; AR multipliers adjust distance and scale.
const HEATMAP_FORWARD_M_VR = 0.70;
const HEATMAP_FORWARD_M_AR = 1.10;
const AR_UI_SCALE          = 0.65;   // applied to every panel/button mesh in AR

const HEATMAP_FORWARD_M  = (MODE === 'ar') ? HEATMAP_FORWARD_M_AR : HEATMAP_FORWARD_M_VR;
const UI_SCALE           = (MODE === 'ar') ? AR_UI_SCALE          : 1.0;

const HEATMAP_W          = 0.40;    // 40cm panel matches FlexGrid 15:4 aspect
const HEATMAP_H          = 0.12;

// Menu panel sits below the heatmap. Flat plane with rectangular buttons in
// a 3-wide x 2-tall grid, hit-tested by raycast from the off-hand. Top row
// = "what's running right now" toggles (REC / SESSION / PREDICT). Bottom
// row = actions and system (TRAIN / RECENTER / EXIT VR).
const MENU_COLS          = 3;
const MENU_ROWS          = 2;
const MENU_BTN_W         = 0.16;
const MENU_BTN_H         = 0.07;
const MENU_BTN_GAP       = 0.012;
const MENU_PAD           = 0.020;
const MENU_W             = MENU_COLS * MENU_BTN_W + (MENU_COLS - 1) * MENU_BTN_GAP + 2 * MENU_PAD;
const MENU_H             = MENU_ROWS * MENU_BTN_H + (MENU_ROWS - 1) * MENU_BTN_GAP + 2 * MENU_PAD;
const MENU_OFFSET_DOWN   = 0.28;    // panel center this far below the heatmap
const MENU_TILT_DEG      = 18;      // tilt top of panel toward head so it's readable

// REAL vs PREDICTED finger-curl comparison panel. Sits in the gap between
// the heatmap bottom and the menu top. Per-finger pair of vertical bars
// (REAL green, PRED amber) for index/middle/ring/pinky -- the four
// forearm-driven fingers our model targets. Thumb intentionally omitted.
const COMPARE_W            = HEATMAP_W;
const COMPARE_H            = 0.05;
const COMPARE_OFFSET_DOWN  = 0.14;

const STATUS_ROW_DOWN    = 0.41;    // status strip sits below the menu
const STATUS_W           = HEATMAP_W;
const STATUS_H           = 0.045;
const STATUS_FADE_MS     = 6000;    // status text fully fades after 6s of no update

const REPORT_HZ          = 30;      // throttle /ws/quest sends (~30Hz is plenty)
const BUTTON_COOLDOWN_MS = 600;     // min gap between two select-presses on a button

// Ray pointer: thin line forward from each XR controller, shortened to the
// hit point when it crosses a menu button. Standard WebXR pattern. We keep
// the idle ray short (0.8m) so it doesn't shoot off into the room and look
// laser-pointer-y -- it should read more as "where my finger is aimed" than
// "where this beam ends up." On hover, it extends to the hit point.
const RAY_IDLE_LEN_M     = 0.8;
const RAY_COLOR_IDLE     = 0x4a7ab8;     // muted blue
const RAY_COLOR_HOVER    = 0xfbbf24;     // amber on hit

const BUTTON_FLASH_MS    = 180;

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
            xrOK = await navigator.xr.isSessionSupported(XR_SESSION_TYPE);
        } catch (e) { xrOK = false; }
    }
    setCheck('check-xr', xrOK ? 'ok' : 'bad',
             xrOK ? `WebXR ${XR_SESSION_TYPE} supported`
                  : `WebXR ${XR_SESSION_TYPE} not available (open in Quest Browser)`);
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
// Sync slate: big high-contrast splash that appears for SYNC_SLATE_MS ms
// at REC press. Visible in the headset's screen recording so the operator
// can frame-accurately pair the video to a CSV row when scrubbing later.
let slateMesh, slateCanvas, slateCtx, slateTex;
let slateShownUntil = 0;
const SYNC_SLATE_MS = 2500;
const SLATE_W = 0.60;
const SLATE_H = 0.20;
let compareMesh, compareCanvas, compareCtx, compareTex;
// Per-finger max wrist->tip distance ever observed. We use this as the
// "fully extended" reference so the curl normalization adapts to the
// user's hand size without needing calibration. Starts conservative.
const fingerMaxExtended = [0.105, 0.110, 0.105, 0.090];   // index, middle, ring, pinky
let pinchIndicator;
let armGroup;                                  // captured-hand joint spheres (blue)
let armJointMeshes = new Map();                // joint-name -> sphere mesh
let offHandGroup;                              // off-hand joint spheres (green)
let offHandJointMeshes = new Map();            // joint-name -> sphere mesh
let ghostGroup;                                // model-predicted hand (amber, translucent)
let ghostJointMeshes = new Map();              // joint-name -> sphere mesh
let placed = false;                            // anchors set on first XRFrame

// Menu panel + buttons. Each button is a rectangular Mesh (PlaneGeometry +
// canvas texture) parented to menuPanel so they move together. Hit-tested
// by raycast from the off-hand controller; activated by the pinch
// (XRInputSource "select") event on the same hand.
let menuPanel;
const buttons = {};   // name -> { mesh, canvas, ctx, tex, label, isActive(),
                      //            onActivate(), lastActivateAt }

// One Three.js Object3D per XR controller (positioned by WebXR each frame).
// We attach a thin line to each so the user sees where their hand is pointing.
const controllers = [];          // controllers[i].userData.handedness = "left"|"right"
const controllerRays = [];       // line meshes parallel to controllers[]
const raycaster = new THREE.Raycaster();
let hoveredButton = null;        // whichever button the off-hand ray is currently over

// Server-derived state (mirrored into button visuals each frame from /ws/live)
const uiState = {
    recording: false,
    sessionActive: false,
    sessionId: null,
    training: false,
    inferenceLoaded: false,        // a model is loaded on the server
    inferenceEnabled: false,       // and predictions are actively flowing
    inferenceModel: null,          // model name string for status
    // pinch is the hands-free fallback for REC only
    pinchStart: 0,
    lastPinchToggleAt: 0,
};

let lastReportAt = 0;
let lastStatusAt = 0;
let lastStatusText = '';

function initScene() {
    scene = new THREE.Scene();
    // In AR mode, leave the background null so passthrough shows through.
    // In VR mode, set the dark background as before. Three.js handles the
    // alpha-clear automatically when an immersive-ar session is active, but
    // a non-null scene.background would still paint over it.
    scene.background = (MODE === 'ar') ? null : new THREE.Color(0x05060a);

    camera = new THREE.PerspectiveCamera(70, window.innerWidth / window.innerHeight,
                                          0.05, 50);
    camera.position.set(0, 1.6, 0);

    // alpha: true is REQUIRED for AR mode. Without it the WebGL framebuffer
    // is opaque, so passthrough has nothing to composite behind -- you'd see
    // the dark VR background even though the session granted immersive-ar.
    // In VR mode alpha: true is fine too (we draw a solid background) so we
    // enable it unconditionally rather than conditionally on MODE.
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.xr.enabled = true;
    if (MODE === 'ar') renderer.setClearAlpha(0);   // belt-and-suspenders: explicit transparent clear
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

    // Menu panel + 3x2 grid of rectangular buttons. Top row = toggles for
    // active state (recording / session / inference). Bottom row = actions
    // and system. Hit-tested by raycast from the off-hand controller;
    // activated by pinch (XRInputSource "select" event). Pinch-to-record
    // on the captured arm still works independently.
    createMenuPanel();
    // Row 0: state toggles
    createMenuButton('REC',     'REC',     () => uiState.recording,        toggleRecording, 0, 0);
    createMenuButton('SESSION', 'SESSION', () => uiState.sessionActive,    toggleSession,   0, 1);
    createMenuButton('PREDICT', 'PREDICT', () => uiState.inferenceEnabled, togglePredict,   0, 2);
    // Row 1: actions
    createMenuButton('TRAIN',   'TRAIN',    () => uiState.training, runTrain,    1, 0);
    createMenuButton('RECENTER','RECENTER', () => false,            recenterUI,  1, 1);
    createMenuButton('EXIT',    'EXIT VR',  () => false,            exitVR,      1, 2);

    // Controller objects (one per hand). renderer.xr.getController(i) returns
    // a Three.js Object3D whose transform is updated each frame by the XR
    // system to match the corresponding XRInputSource's targetRaySpace.
    // We attach a line to visualize the ray and listen for select (pinch).
    for (let i = 0; i < 2; i++) {
        const ctrl = renderer.xr.getController(i);
        scene.add(ctrl);
        ctrl.userData.handedness = null;
        ctrl.addEventListener('connected', (event) => {
            ctrl.userData.handedness = event.data?.handedness || null;
        });
        ctrl.addEventListener('disconnected', () => {
            ctrl.userData.handedness = null;
        });
        ctrl.addEventListener('selectstart', () => onControllerSelect(ctrl));
        const lineGeo = new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(0, 0, 0),
            new THREE.Vector3(0, 0, -1),
        ]);
        const lineMat = new THREE.LineBasicMaterial({
            color: RAY_COLOR_IDLE, transparent: true, opacity: 0.75,
        });
        const line = new THREE.Line(lineGeo, lineMat);
        line.scale.z = RAY_IDLE_LEN_M;
        ctrl.add(line);
        controllers.push(ctrl);
        controllerRays.push(line);
    }

    // REAL vs PRED comparison panel. Lives between the heatmap and the menu.
    // Hidden until inference is running AND a captured hand is being tracked
    // (otherwise we have nothing to compare). See drawCompare.
    compareCanvas = document.createElement('canvas');
    compareCanvas.width = 800; compareCanvas.height = 100;
    compareCtx = compareCanvas.getContext('2d');
    compareTex = new THREE.CanvasTexture(compareCanvas);
    compareTex.colorSpace = THREE.SRGBColorSpace;
    compareMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(COMPARE_W, COMPARE_H),
        new THREE.MeshBasicMaterial({ map: compareTex, transparent: true }));
    compareMesh.visible = false;
    scene.add(compareMesh);
    drawCompare(null, null);

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

    // Sync slate: 60 x 20 cm panel for the SYNC_SLATE_MS splash at REC press.
    // Hidden by default; brought to life by showSyncSlate(filename, unixMs).
    slateCanvas = document.createElement('canvas');
    slateCanvas.width = 1600; slateCanvas.height = 540;
    slateCtx = slateCanvas.getContext('2d');
    slateTex = new THREE.CanvasTexture(slateCanvas);
    slateTex.colorSpace = THREE.SRGBColorSpace;
    slateMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(SLATE_W, SLATE_H),
        new THREE.MeshBasicMaterial({ map: slateTex, transparent: true,
                                       depthWrite: false }));
    slateMesh.visible = false;
    scene.add(slateMesh);

    // Pinch progress ring (drawn on a small canvas, pinned to the captured hand's
    // wrist each frame; opacity scales with pinch hold time)
    // Pinch progress ring -- pinned to the captured hand's index tip when the
    // captured arm is pinching. Bigger + more saturated than v1.x so it's
    // easy to spot at arm's length while you're focusing on the gesture.
    pinchIndicator = new THREE.Mesh(
        new THREE.RingGeometry(0.038, 0.046, 32, 1, 0, Math.PI * 2),
        new THREE.MeshBasicMaterial({ color: 0xfacc15, transparent: true,
                                       opacity: 0, side: THREE.DoubleSide }));
    pinchIndicator.visible = false;
    scene.add(pinchIndicator);

    // In AR mode shrink every panel/group uniformly so they read as small
    // overlays against the real-world background. Slate stays full-size
    // deliberately -- it's designed to dominate the 2.5s video-sync frame.
    if (UI_SCALE !== 1.0) {
        for (const obj of [heatmapMesh, headerMesh, compareMesh, statusMesh, menuPanel]) {
            obj.scale.setScalar(UI_SCALE);
        }
    }

    // Joint visualizer spheres for BOTH hands. Captured arm = blue (the
    // hand whose pose we're recording). Off-hand = green (the hand that
    // drives the menu). Distinct colors so the user can tell at a glance
    // which finger spheres belong to which hand. Off-hand spheres are
    // slightly larger so the pointing hand reads more solid even when
    // most of the ray is hidden behind the fingertip.
    const jointGeoSmall = new THREE.SphereGeometry(0.006, 8, 6);
    const jointGeoBig   = new THREE.SphereGeometry(0.0085, 10, 8);

    armGroup = new THREE.Group();
    scene.add(armGroup);
    const armJointMat = new THREE.MeshBasicMaterial({ color: 0x60a5fa });
    for (const name of JOINT_NAMES) {
        const m = new THREE.Mesh(jointGeoSmall, armJointMat);
        m.visible = false;
        armGroup.add(m);
        armJointMeshes.set(name, m);
    }

    offHandGroup = new THREE.Group();
    scene.add(offHandGroup);
    const offHandJointMat = new THREE.MeshBasicMaterial({ color: 0x34d399 });
    for (const name of JOINT_NAMES) {
        const m = new THREE.Mesh(jointGeoBig, offHandJointMat);
        m.visible = false;
        offHandGroup.add(m);
        offHandJointMeshes.set(name, m);
    }

    // Ghost predicted-hand: amber, semi-transparent spheres at the joint
    // positions the model predicts from the current FlexGrid frame. Aligned
    // with the real wrist each frame so the visualization shows predicted
    // SHAPE, not absolute model output (which would be in whatever world
    // frame the recording was made in -- usually not where the user is now).
    const ghostJointGeo = new THREE.SphereGeometry(0.0075, 10, 8);
    const ghostJointMat = new THREE.MeshBasicMaterial({
        color: 0xfbbf24, transparent: true, opacity: 0.7, depthWrite: false,
    });
    ghostGroup = new THREE.Group();
    scene.add(ghostGroup);
    for (const name of JOINT_NAMES) {
        const m = new THREE.Mesh(ghostJointGeo, ghostJointMat);
        m.visible = false;
        ghostGroup.add(m);
        ghostJointMeshes.set(name, m);
    }

    window.addEventListener('resize', () => {
        if (renderer.xr.isPresenting) return;  // XR owns the projection then
        camera.aspect = window.innerWidth / window.innerHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(window.innerWidth, window.innerHeight);
    });
}

function createMenuPanel() {
    menuPanel = new THREE.Group();
    // Translucent dark plate behind the buttons so the menu reads as one
    // surface rather than four floating tiles.
    const plate = new THREE.Mesh(
        new THREE.PlaneGeometry(MENU_W, MENU_H),
        new THREE.MeshBasicMaterial({
            color: 0x0d1117, transparent: true, opacity: 0.55,
            side: THREE.DoubleSide,
        }));
    plate.position.z = -0.001;   // sit just behind the buttons (which are at z=0)
    menuPanel.add(plate);
    scene.add(menuPanel);
}

function createMenuButton(name, label, isActive, onActivate, row, col) {
    const canvas = document.createElement('canvas');
    canvas.width = 384; canvas.height = 144;
    const ctx = canvas.getContext('2d');
    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    const mat = new THREE.MeshBasicMaterial({
        map: tex, transparent: true, side: THREE.DoubleSide, depthWrite: false,
    });
    const mesh = new THREE.Mesh(
        new THREE.PlaneGeometry(MENU_BTN_W, MENU_BTN_H), mat);
    // Grid laid out centered on the panel using the MENU_COLS/MENU_ROWS
    // constants so adding/removing a row stays cheap.
    const xOffset = (col - (MENU_COLS - 1) / 2) * (MENU_BTN_W + MENU_BTN_GAP);
    const yOffset = -(row - (MENU_ROWS - 1) / 2) * (MENU_BTN_H + MENU_BTN_GAP);
    mesh.position.set(xOffset, yOffset, 0);
    mesh.userData.buttonName = name;  // raycaster will look this up
    menuPanel.add(mesh);
    buttons[name] = {
        name, label, mesh, canvas, ctx, tex, mat,
        isActive, onActivate, lastActivateAt: 0,
    };
    drawMenuButton(buttons[name], false);
}

function drawMenuButton(btn, hovered) {
    const ctx = btn.ctx;
    const W = btn.canvas.width, H = btn.canvas.height;
    ctx.clearRect(0, 0, W, H);
    const active = !!btn.isActive();
    const flashing = (btn.flashUntil || 0) > performance.now();

    // Rounded-rect background. Priority: flash > active > hovered > idle.
    // Flash is the brief white burst right after a successful select; it
    // overrides everything else so the user sees confirmation independent
    // of whatever state-change the action triggers (which might be
    // imperceptible if the server hasn't responded yet).
    let bg;
    if (flashing)     bg = '#ffffff';
    else if (active)  bg = '#ef4444';
    else if (hovered) bg = '#1f6feb';
    else              bg = '#2a3142';
    ctx.fillStyle = bg;
    const r = 24;
    ctx.beginPath();
    ctx.moveTo(r, 0);
    ctx.lineTo(W - r, 0); ctx.arcTo(W, 0, W, r, r);
    ctx.lineTo(W, H - r); ctx.arcTo(W, H, W - r, H, r);
    ctx.lineTo(r, H);     ctx.arcTo(0, H, 0, H - r, r);
    ctx.lineTo(0, r);     ctx.arcTo(0, 0, r, 0, r);
    ctx.closePath();
    ctx.fill();

    // Label: REC <-> STOP swap so the button tells you what tapping it does next
    const labelText = (btn.name === 'REC' && active) ? 'STOP' : btn.label;
    ctx.fillStyle = flashing ? '#0d1117' : '#f0f4f8';   // dark text on white flash
    ctx.font = 'bold 72px system-ui';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(labelText, W / 2, H / 2);

    btn.tex.needsUpdate = true;
}

function exitVR() {
    const session = renderer.xr.getSession();
    if (session) session.end();
}

// Toggle live inference on/off. Server-side: POST /api/inference/enabled
// {enabled: bool}. We mirror inferenceEnabled from /ws/live snapshot so
// the button's active color reflects whatever the server actually thinks.
async function togglePredict() {
    if (!uiState.inferenceLoaded) {
        setStatus('no model loaded -- train one first (TRAIN button)');
        return;
    }
    const next = !uiState.inferenceEnabled;
    try {
        const r = await fetch('/api/inference/enabled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: next }),
        });
        if (r.ok) {
            const data = await r.json();
            setStatus(`predict ${data.enabled ? 'ON' : 'paused'}` +
                      (data.model ? ` (${data.model})` : ''));
        } else {
            setStatus(`predict toggle failed: HTTP ${r.status}`);
        }
    } catch (e) {
        setStatus(`predict toggle error: ${e.message}`);
    }
}

// Re-anchor the menu + heatmap to wherever the head is right now. Useful
// when the user has shifted in their chair or moved around the room since
// session start. Setting `placed = false` makes the next XRFrame call
// placeAnchors with the current viewer pose.
function recenterUI() {
    placed = false;
    setStatus('UI re-centered to your current view');
}

// Pinch on a controller -> activate whichever button the ray is currently over,
// IF that controller is the off-hand. The captured-arm pinch is handled by
// detectPinchAndToggle (1-second hold required for record toggle) so we
// don't double-fire here.
function onControllerSelect(ctrl) {
    const handedness = ctrl.userData?.handedness;
    if (handedness === ARM) return;       // captured arm: handled elsewhere
    if (!hoveredButton) return;
    const now = performance.now();
    if (now - hoveredButton.lastActivateAt < BUTTON_COOLDOWN_MS) return;
    hoveredButton.lastActivateAt = now;
    // Brief white flash so the user gets visual confirmation that the press
    // registered, separate from whatever state-change the action triggers.
    hoveredButton.flashUntil = now + BUTTON_FLASH_MS;
    try { hoveredButton.onActivate(); }
    catch (e) { console.error(`button ${hoveredButton.name} failed:`, e); }
}

// Per-frame: raycast each controller against the menu buttons, highlight the
// hovered one, shorten the visible ray to the hit point. Only the off-hand's
// ray is visible -- the captured arm's ray would clutter the view and risk
// hovering buttons while you're trying to perform a gesture.
const _tmpMat = new THREE.Matrix4();
function updateRaycast() {
    hoveredButton = null;
    const buttonMeshes = Object.values(buttons).map(b => b.mesh);

    for (let i = 0; i < controllers.length; i++) {
        const ctrl = controllers[i];
        const line = controllerRays[i];
        const handedness = ctrl.userData?.handedness;
        // Hide the captured-arm's ray + skip its raycast
        if (!handedness || handedness === ARM) {
            line.visible = false;
            continue;
        }
        line.visible = true;

        _tmpMat.identity().extractRotation(ctrl.matrixWorld);
        raycaster.ray.origin.setFromMatrixPosition(ctrl.matrixWorld);
        raycaster.ray.direction.set(0, 0, -1).applyMatrix4(_tmpMat);

        const hits = raycaster.intersectObjects(buttonMeshes, false);
        if (hits.length > 0) {
            const hit = hits[0];
            const name = hit.object.userData.buttonName;
            const btn = buttons[name];
            if (btn) {
                hoveredButton = btn;
                line.scale.z = hit.distance;
                line.material.color.setHex(RAY_COLOR_HOVER);
                continue;
            }
        }
        // No hit: restore default-length idle-colored ray
        line.scale.z = RAY_IDLE_LEN_M;
        line.material.color.setHex(RAY_COLOR_IDLE);
    }
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

// Render the SYNC slate canvas. Big, high-contrast text so it's readable in
// a re-watched screen recording when paused on the slate frame. Layout:
//   line 1: SYNC                (giant)
//   line 2: <filename>          (medium, monospace)
//   line 3: <unix-ms timestamp> (medium, monospace)
function drawSlate(filename, unixMs) {
    const ctx = slateCtx;
    const W = slateCanvas.width, H = slateCanvas.height;
    ctx.clearRect(0, 0, W, H);

    // High-contrast yellow background -- pops in both VR and AR (passthrough)
    ctx.fillStyle = '#fbbf24';
    ctx.fillRect(0, 0, W, H);

    // Black border for definition against bright real-world backgrounds in AR
    ctx.strokeStyle = '#0d1117';
    ctx.lineWidth = 12;
    ctx.strokeRect(6, 6, W - 12, H - 12);

    ctx.fillStyle = '#0d1117';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    ctx.font = 'bold 140px system-ui';
    ctx.fillText('SYNC', W / 2, 130);

    ctx.font = 'bold 56px ui-monospace, "SF Mono", Menlo, Consolas, monospace';
    ctx.fillText(filename, W / 2, 280);

    ctx.font = '52px ui-monospace, "SF Mono", Menlo, Consolas, monospace';
    ctx.fillText(`t = ${unixMs}`, W / 2, 380);

    ctx.font = '32px system-ui';
    ctx.fillText('pair this video frame with the CSV row at this timestamp',
                 W / 2, 470);

    slateTex.needsUpdate = true;
}

// Trigger the slate splash. Visible for SYNC_SLATE_MS, then auto-hidden by
// updateSlateVisibility() in the frame loop.
function showSyncSlate(filename, unixMs) {
    drawSlate(filename, unixMs);
    slateMesh.visible = true;
    slateShownUntil = performance.now() + SYNC_SLATE_MS;
}

function updateSlateVisibility() {
    if (slateMesh.visible && performance.now() > slateShownUntil) {
        slateMesh.visible = false;
    }
}

// ---------------------------------------------------------------------------
// Finger curl: derived from wrist <-> finger-tip distance, normalized to a
// per-user [0..1] curl where 1 = fully curled (tip near wrist), 0 = fully
// extended. Used both for REAL (computed from XRHand each frame) and PRED
// (computed from the model's flat predicted-joint vector). Index/middle/
// ring/pinky only -- the four fingers FlexGrid can actually see.
// ---------------------------------------------------------------------------

// Indices into the JOINT_NAMES order. Wrist is 0, then 4 thumb joints,
// then 5 each for index/middle/ring/pinky -- so tips are at 9, 14, 19, 24.
const TIP_INDICES_FMRP = [9, 14, 19, 24];

function curlFromDistance(dist, fingerIdx) {
    if (dist > fingerMaxExtended[fingerIdx]) fingerMaxExtended[fingerIdx] = dist;
    const maxD = fingerMaxExtended[fingerIdx];
    const minD = maxD * 0.45;   // empirical fully-curled estimate (~45% of extended)
    const range = maxD - minD;
    if (range <= 0) return 0;
    return Math.max(0, Math.min(1, 1 - (dist - minD) / range));
}

function realCurls(frame, refSpace, hand) {
    if (!hand) return null;
    const wp = jointPose(frame, refSpace, hand, 'wrist');
    if (!wp) return null;
    const w = wp.transform.position;
    const out = [];
    const names = ['index-finger-tip', 'middle-finger-tip',
                   'ring-finger-tip',  'pinky-finger-tip'];
    for (let i = 0; i < 4; i++) {
        const tp = jointPose(frame, refSpace, hand, names[i]);
        if (!tp) { out.push(null); continue; }
        const t = tp.transform.position;
        const d = Math.hypot(t.x - w.x, t.y - w.y, t.z - w.z);
        out.push(curlFromDistance(d, i));
    }
    return out;
}

function predictedCurls(values) {
    // The inference engine emits the same flat joint vector format the Quest
    // sends: 25 joints * 7 floats = 175 numbers. wrist = 0, finger tips at
    // [9, 14, 19, 24]. Each joint is [px, py, pz, rx, ry, rz, rw] -- we only
    // need positions for the curl metric.
    if (!values || values.length < 25 * 7) return null;
    const wx = values[0], wy = values[1], wz = values[2];
    const out = [];
    for (let i = 0; i < 4; i++) {
        const base = TIP_INDICES_FMRP[i] * 7;
        const tx = values[base], ty = values[base + 1], tz = values[base + 2];
        const d = Math.hypot(tx - wx, ty - wy, tz - wz);
        out.push(curlFromDistance(d, i));
    }
    return out;
}

function drawCompare(real, pred) {
    const ctx = compareCtx;
    const W = compareCanvas.width, H = compareCanvas.height;
    ctx.clearRect(0, 0, W, H);

    // Plate
    ctx.fillStyle = 'rgba(20, 24, 32, 0.85)';
    ctx.fillRect(0, 0, W, H);

    // 4 columns, one per finger
    const labels = ['INDEX', 'MIDDLE', 'RING', 'PINKY'];
    const colW = W / 4;
    const barAreaTop = 26;
    const barAreaH   = 56;

    for (let i = 0; i < 4; i++) {
        const cx = i * colW;

        // Column header
        ctx.fillStyle = '#8b96a8';
        ctx.font = 'bold 16px system-ui';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(labels[i], cx + colW / 2, 6);

        // Two side-by-side bars (R then P)
        const barW = (colW - 48) / 2;
        const xR = cx + 16;
        const xP = xR + barW + 16;

        // Track backgrounds
        ctx.fillStyle = '#1f2228';
        ctx.fillRect(xR, barAreaTop, barW, barAreaH);
        ctx.fillRect(xP, barAreaTop, barW, barAreaH);

        // Real fill (green)
        if (real && real[i] != null) {
            const fh = barAreaH * real[i];
            ctx.fillStyle = '#34d399';
            ctx.fillRect(xR, barAreaTop + barAreaH - fh, barW, fh);
        }
        // Predicted fill (amber)
        if (pred && pred[i] != null) {
            const fh = barAreaH * pred[i];
            ctx.fillStyle = '#fbbf24';
            ctx.fillRect(xP, barAreaTop + barAreaH - fh, barW, fh);
        }

        // Tiny letter under each bar so the colors stay legend-able
        ctx.font = 'bold 11px system-ui';
        ctx.textBaseline = 'top';
        ctx.fillStyle = '#34d399';
        ctx.fillText('R', xR + barW / 2, barAreaTop + barAreaH + 3);
        ctx.fillStyle = '#fbbf24';
        ctx.fillText('P', xP + barW / 2, barAreaTop + barAreaH + 3);
    }

    compareTex.needsUpdate = true;
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

    // Place heatmap HEATMAP_FORWARD_M in front, slightly below eye height.
    // Vertical offsets are scaled by UI_SCALE so panel-to-panel gaps shrink
    // proportionally with panel sizes in AR mode (otherwise small panels
    // would have weirdly large empty space between them).
    const heatPos = headPos.clone().addScaledVector(headFwd, HEATMAP_FORWARD_M);
    heatPos.y -= 0.10 * UI_SCALE;
    heatmapMesh.position.copy(heatPos);
    heatmapMesh.lookAt(headPos);

    // Header centered above the heatmap
    headerMesh.position.copy(heatPos).add(new THREE.Vector3(0, HEATMAP_H * 0.6 * UI_SCALE, 0));
    headerMesh.lookAt(headPos);

    // REAL vs PRED comparison panel sits between the heatmap and the menu.
    // Same tilt as the menu so they look like one continuous surface tilted
    // toward the head.
    const comparePos = heatPos.clone().add(new THREE.Vector3(0, -COMPARE_OFFSET_DOWN * UI_SCALE, 0));
    compareMesh.position.copy(comparePos);
    compareMesh.lookAt(headPos);
    compareMesh.rotateX(THREE.MathUtils.degToRad(MENU_TILT_DEG));

    // Menu panel sits below the heatmap. Tilted slightly toward the head so
    // the buttons are readable + ray-hits feel natural even when the user is
    // looking down at it. The lookAt aims the +Z axis at the head; then a
    // small extra X-axis rotation tilts the top edge toward the user.
    const menuPos = heatPos.clone().add(new THREE.Vector3(0, -MENU_OFFSET_DOWN * UI_SCALE, 0));
    menuPanel.position.copy(menuPos);
    menuPanel.lookAt(headPos);
    menuPanel.rotateX(THREE.MathUtils.degToRad(MENU_TILT_DEG));

    // Status strip below the menu (also tilted to match for readability)
    statusMesh.position.copy(heatPos).add(new THREE.Vector3(0, -STATUS_ROW_DOWN * UI_SCALE, 0));
    statusMesh.lookAt(headPos);
    statusMesh.rotateX(THREE.MathUtils.degToRad(MENU_TILT_DEG));

    // Sync slate sits centered in the user's primary view (in front of the
    // heatmap, between them and it). When triggered at REC press it pops up
    // big and obvious so the headset's screen recording captures a clean
    // sync frame. Slightly closer than the heatmap so it visually takes
    // precedence during the splash.
    slateMesh.position.copy(headPos).addScaledVector(headFwd, 0.55);
    slateMesh.lookAt(headPos);

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

function updateHandVisualizer(frame, refSpace, hand, meshesMap) {
    for (const [name, mesh] of meshesMap) {
        const p = jointPose(frame, refSpace, hand, name);
        if (!p) { mesh.visible = false; continue; }
        mesh.visible = true;
        mesh.position.set(p.transform.position.x,
                          p.transform.position.y,
                          p.transform.position.z);
    }
}
function hideHandVisualizer(meshesMap) {
    for (const m of meshesMap.values()) m.visible = false;
}

// Reused per-frame so we don't churn allocations in the XR loop.
const _ghostTmpVec = new THREE.Vector3();
const _ghostPredWristPos = new THREE.Vector3();
const _ghostRealWristPos = new THREE.Vector3();
const _ghostPredQuat = new THREE.Quaternion();
const _ghostRealQuat = new THREE.Quaternion();
const _ghostDeltaQuat = new THREE.Quaternion();

function updateGhostHand(predValues, realWristPos, realWristQuat) {
    // Render the model's predicted joint positions as a ghost hand pinned
    // to the user's actual wrist + orientation. The model was trained on
    // absolute world poses from wherever the recordings happened to be, so
    // the raw predicted positions float in that recording-time world frame
    // rather than the user's current frame. To make the visualization show
    // predicted SHAPE rather than absolute position, we transform each
    // predicted joint from "predicted wrist frame" into "real wrist frame":
    //   pos_in_real_frame = real_wrist_pos + delta_quat * (pred_pos - pred_wrist_pos)
    //   where delta_quat = real_wrist_quat * inverse(pred_wrist_quat)
    // Position-only alignment was the v1.7 default; v1.8 adds the rotation
    // so the ghost hand actually faces the same way as your real hand.
    if (!predValues || predValues.length < 25 * 7 || !realWristPos) {
        for (const m of ghostJointMeshes.values()) m.visible = false;
        return;
    }
    _ghostPredWristPos.set(predValues[0], predValues[1], predValues[2]);
    _ghostRealWristPos.set(realWristPos.x, realWristPos.y, realWristPos.z);

    let useRotation = false;
    if (realWristQuat) {
        _ghostPredQuat.set(predValues[3], predValues[4], predValues[5], predValues[6]);
        _ghostRealQuat.set(realWristQuat.x, realWristQuat.y, realWristQuat.z, realWristQuat.w);
        // delta = real * inverse(pred). Apply to (pred_pos - pred_wrist_pos)
        // to land in the real-wrist's frame.
        _ghostDeltaQuat.copy(_ghostRealQuat).multiply(_ghostPredQuat.clone().invert());
        useRotation = true;
    }

    let i = 0;
    for (const [, mesh] of ghostJointMeshes) {
        const base = i * 7;
        _ghostTmpVec.set(predValues[base], predValues[base + 1], predValues[base + 2])
                    .sub(_ghostPredWristPos);
        if (useRotation) _ghostTmpVec.applyQuaternion(_ghostDeltaQuat);
        _ghostTmpVec.add(_ghostRealWristPos);
        mesh.position.copy(_ghostTmpVec);
        mesh.visible = true;
        i++;
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

function updateButtonVisual() {
    // Repaint each button's canvas reflecting (a) the latest state and (b)
    // whether it's currently the hovered target of the off-hand ray.
    for (const btn of Object.values(buttons)) {
        drawMenuButton(btn, btn === hoveredButton);
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
    // Header text: while recording, include filename + ms clock so the
    // headset's screen recording captures sync info every frame the user
    // happens to look at the heatmap. Without it, the operator would have
    // to memorize which capture corresponds to which video shoot.
    const rec = snap.recording;
    if (rec) {
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, '0');
        const mm = String(now.getMinutes()).padStart(2, '0');
        const ss = String(now.getSeconds()).padStart(2, '0');
        const ms = String(now.getMilliseconds()).padStart(3, '0');
        drawHeader(`REC · ${hh}:${mm}:${ss}.${ms} · ${rec.filename}`, true);
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
    // Inference state: drives the PREDICT button's color + label, and the
    // togglePredict button's "no model loaded" precheck.
    const inf = snap.inference || {};
    uiState.inferenceLoaded  = !!inf.model;
    uiState.inferenceEnabled = !!inf.enabled;
    uiState.inferenceModel   = inf.model || null;
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
                // Fire the sync slate so the headset's screen recording
                // captures a clean frame-accurate pairing point. The Unix-
                // ms timestamp embedded in the slate lets you locate the
                // exact CSV row when scrubbing the video later.
                showSyncSlate(data.filename, Date.now());
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
            // After a successful train+activate, kick inference on so the
            // user sees predictions immediately. Server's default policy
            // (from commit bd1b68a) is paused-on-load, but in VR there's
            // no obvious second click to enable it -- TRAIN already implies
            // "I want this model running".
            let predictTail = '';
            if (result.active) {
                try {
                    const er = await fetch('/api/inference/enabled', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ enabled: true }),
                    });
                    if (er.ok) predictTail = ' · predict ON';
                } catch (e) { /* non-fatal */ }
            }
            setStatus(`trained: R²=${r2str}` +
                      (result.active ? ' · model loaded ✓' : ' (saved only)') +
                      predictTail);
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

    let capturedHand = null, offHand = null;
    for (const input of session.inputSources) {
        if (!input.hand) continue;
        if (input.handedness === ARM)            capturedHand = input.hand;
        else if (input.handedness && !offHand)   offHand = input.hand;
    }
    if (capturedHand) {
        captureAndSend(frame, refSpace, capturedHand, timestamp);
        updateHandVisualizer(frame, refSpace, capturedHand, armJointMeshes);
        detectPinchAndToggle(frame, refSpace, capturedHand, timestamp);
    } else {
        hideHandVisualizer(armJointMeshes);
        pinchIndicator.visible = false;
    }
    if (offHand) {
        updateHandVisualizer(frame, refSpace, offHand, offHandJointMeshes);
    } else {
        hideHandVisualizer(offHandJointMeshes);
    }

    // REAL vs PRED comparison + ghost hand: visible only when we have
    // something to compare (model loaded AND running AND captured hand
    // tracked). The user explicitly wants to see model error after
    // training, so both pop in the moment PREDICT turns on.
    const predValues = latestSnapshot?.inference?.piston_values || null;
    const showCompare = capturedHand && uiState.inferenceEnabled && predValues;
    compareMesh.visible = !!showCompare;
    if (showCompare) {
        const real = realCurls(frame, refSpace, capturedHand);
        const pred = predictedCurls(predValues);
        drawCompare(real, pred);
        // Ghost hand pinned to the real wrist (position AND orientation as
        // of v1.8) so the user sees the model's shape prediction in the
        // same frame as their actual hand.
        const wp = jointPose(frame, refSpace, capturedHand, 'wrist');
        updateGhostHand(predValues,
                        wp ? wp.transform.position : null,
                        wp ? wp.transform.orientation : null);
    } else {
        for (const m of ghostJointMeshes.values()) m.visible = false;
    }

    updateRaycast();
    updateButtonVisual();
    updateFromSnapshot(latestSnapshot, timestamp);
    updateSlateVisibility();

    renderer.render(scene, camera);
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

function bootVRButton() {
    initScene();
    // VRButton requests immersive-vr; ARButton requests immersive-ar.
    // local-floor is required in VR (we anchor to floor height) but optional
    // in AR (the headset is already in the user's real space; passthrough
    // gives us our own floor). hand-tracking is optional in both -- if the
    // user picks up controllers we still get a basic ray-pointer session.
    const buttonFactory = MODE === 'ar' ? ARButton : VRButton;
    const button = buttonFactory.createButton(renderer, {
        requiredFeatures: MODE === 'ar' ? [] : ['local-floor'],
        optionalFeatures: MODE === 'ar'
            ? ['local-floor', 'hand-tracking']
            : ['hand-tracking'],
    });
    // ARButton/VRButton labels differ slightly ("START AR" vs "ENTER VR").
    // Don't override; the user knows what mode they picked from the URL.
    document.getElementById('enter-vr-mount').appendChild(button);

    renderer.xr.addEventListener('sessionstart', () => {
        document.getElementById('landing').style.display = 'none';
        renderer.domElement.style.display = 'block';
        placed = false;
        // Log the granted session's environment-blend mode so we can tell
        // if Quest gave us what we asked for. Expected:
        //   immersive-vr  -> 'opaque'
        //   immersive-ar  -> 'alpha-blend' or 'additive' (Quest 3 = alpha-blend)
        // If MODE === 'ar' but blend mode is 'opaque', Quest fell back to VR
        // and the background will still be black regardless of alpha clearing.
        const session = renderer.xr.getSession();
        console.log('[openmuscle-vr] XR session started.',
                    'requested mode:', XR_SESSION_TYPE,
                    'blend mode:', session?.environmentBlendMode);
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

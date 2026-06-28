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
// Two-hand capture: ?arm=both streams BOTH hands (quest-left + quest-right) so
// the PC can record two bands at once, each matched to its own hand. Recording is
// started from the PC (no free off-hand for the in-VR menu during a two-hand run).
const BOTH_HANDS = params.get('arm') === 'both';
// Debug mode: a panel that surfaces "is everything working" for a two-hand AR
// capture (both hands tracked, per-band Hz/battery, recording match, sockets).
// Toggled by ?debug=1 or the DEBUG menu button.
const DEBUG_PARAM = params.get('debug') === '1';
// MODE: 'vr' (fully immersive black background, the v1.x default, used for
// deliberate gesture training in a controlled space) vs 'ar' (passthrough
// background, real world visible behind our panels, used for field-capture
// sessions during real activities). See docs/vr-setup.md for the use cases.
const MODE = params.get('mode') === 'ar' ? 'ar' : 'vr';
const XR_SESSION_TYPE = MODE === 'ar' ? 'immersive-ar' : 'immersive-vr';
const PINCH_THRESHOLD_M  = 0.025;   // 2.5 cm index-tip <-> thumb-tip
const PINCH_HOLD_MS      = 1000;    // hold this long to toggle recording (captured arm)
const DRAG_END_GRACE_MS  = 200;     // bridge a brief pinch-release flicker mid-drag

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
// a 3-wide x 3-tall grid, hit-tested by raycast from the off-hand. Row 0
// = "what's running right now" toggles (REC / SESSION / PREDICT). Row 1
// = actions (TRAIN / SETUP / RECENTER). Row 2 = system (EXIT VR). SETUP
// opens the config panel (capture replay etc.); the layout math below is
// driven by MENU_COLS/MENU_ROWS so adding the row stayed automatic.
const MENU_COLS          = 3;
const MENU_ROWS          = 3;
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

// Status strip sits below the menu. Pushed down by one button-row's height
// (MENU_BTN_H + MENU_BTN_GAP) when the grid grew from 2 to 3 rows, so the
// taller menu's bottom row keeps its ~1cm clearance above the strip instead
// of overlapping it. (Was 0.41 for the 2-row grid.)
const STATUS_ROW_DOWN    = 0.41 + (MENU_BTN_H + MENU_BTN_GAP);    // 3-row menu
const STATUS_W           = HEATMAP_W;
const STATUS_H           = 0.045;
const BANDSTATUS_W       = HEATMAP_W;   // per-band battery/signal rows under status
const BANDSTATUS_H       = 0.075;
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

// Drag handles: small cubes that sit on the top-left of each movable panel
// group. Off-hand ray hovers + pinches the handle -> the panel follows the
// controller until the pinch releases. Release re-orients the panel toward
// the head so it always ends up facing the user.
const HANDLE_SIZE_M      = 0.030;
const HANDLE_COLOR_IDLE  = 0x6b7280;     // slate gray
const HANDLE_COLOR_HOVER = 0xfbbf24;     // same amber as ray hover -- visual continuity
const HANDLE_COLOR_GRAB  = 0x10b981;     // emerald when actively grabbed

// Collapsed STOP button shown while recording. Replaces the full 3x3 menu
// grid so the user's view isn't dominated by action buttons they don't need
// during natural-activity capture sessions.
const STOP_W             = 0.18;
const STOP_H             = 0.10;

// SETUP config panel (replay-from-headset). A dedicated THREE.Group anchored
// under menuRoot, built like the menu cluster (canvas-mesh panels + raycast
// targets) with its own drag handle. Toggled by the SETUP menu button. The
// REPLAY section lists past captures (GET /api/captures) as canvas-drawn rows
// that are raycast-selectable exactly like the menu buttons, plus a REPLAY /
// STOP REPLAY action button (POST/DELETE /api/simulate/replay) and a speed
// toggle. Sits to the RIGHT of the menu so it doesn't cover the heatmap.
const CONFIG_OFFSET_RIGHT = 0.30;   // panel center this far right of menuRoot
const CONFIG_W            = 0.34;
const CONFIG_TITLE_H      = 0.05;   // header strip ("SETUP / REPLAY")
const CONFIG_ROW_W        = 0.30;   // capture-row + action-button width
const CONFIG_ROW_H        = 0.045;  // one capture row / action button height
const CONFIG_ROW_GAP      = 0.008;
const CONFIG_LIST_ROWS    = 4;      // visible capture rows (no scroll yet; top N)
const CONFIG_PAD          = 0.018;

// BANDS section (in-VR band L/R tagging). Sits ABOVE the REPLAY section: a
// small section header strip + N band rows. Each band row is a wide info label
// (short device_id + role badge + batt/Hz tag, drawn on its own canvas) plus
// three small ray+pinch targets on the right: L | R | clear. Pinch posts to
// /api/discovery/role; the next snapshot's device.role confirms the tag. The
// row count is fixed up front (like the capture rows); drawBandsSection() binds
// each slot to a discovered FlexGrid band or hides it.
const CONFIG_BAND_ROWS    = 3;      // visible band slots (top N flexgrids)
const CONFIG_BAND_BTN_W   = 0.045;  // L / R / clear sub-button width
const CONFIG_BAND_BTN_GAP = 0.006;  // gap between the three sub-buttons
// Info label takes the remaining row width to the left of the 3 sub-buttons.
const CONFIG_BAND_INFO_W  = CONFIG_ROW_W
    - 3 * CONFIG_BAND_BTN_W - 3 * CONFIG_BAND_BTN_GAP;

// TRAIN + MODELS section (per-hand training + model loading from the headset).
// Sits BELOW the REPLAY section: a small section header strip, then a row of
// three TRAIN buttons (TRAIN BOTH wide-ish, TRAIN L, TRAIN R), then two
// model-cycle rows (MODEL L / MODEL R). Each model row is a wide info label
// (current model name for that side, its own canvas) plus a single PICK
// sub-button on the right that cycles to + loads the next trained model. Cycle
// affordance instead of a scroll list -- a full list would blow the panel
// height + need scroll machinery the rest of the panel doesn't have.
const CONFIG_TRAIN_BTN_GAP = CONFIG_BAND_BTN_GAP;   // gap between TRAIN buttons
// TRAIN BOTH takes ~half the row, TRAIN L / TRAIN R split the rest.
const CONFIG_TRAIN_BOTH_W  = CONFIG_ROW_W * 0.48;
const CONFIG_TRAIN_ONE_W   = (CONFIG_ROW_W - CONFIG_TRAIN_BOTH_W
    - 2 * CONFIG_TRAIN_BTN_GAP) / 2;
// Model rows: a PICK sub-button on the right (cycles the model) + a wide info
// label filling the rest of the row, same split style as the band rows.
const CONFIG_MODEL_BTN_W   = 0.075;   // PICK sub-button width
const CONFIG_MODEL_INFO_W  = CONFIG_ROW_W - CONFIG_MODEL_BTN_W - CONFIG_BAND_BTN_GAP;

// Panel height = title + BANDS header + N band rows + N capture list rows
// + action row + speed row + TRAIN header + TRAIN button row + 2 model rows,
// plus gaps/pad. The section headers reuse CONFIG_ROW_H.
const CONFIG_H            = CONFIG_TITLE_H
    + (CONFIG_BAND_ROWS + 1) * CONFIG_ROW_H            // band rows + section header
    + (CONFIG_LIST_ROWS + 2) * CONFIG_ROW_H            // capture rows + action + speed
    + (1 + 1 + 2) * CONFIG_ROW_H                       // TRAIN header + button row + 2 model rows
    + (CONFIG_BAND_ROWS + 1 + CONFIG_LIST_ROWS + 2 + 1 + 1 + 2) * CONFIG_ROW_GAP
    + 2 * CONFIG_PAD;

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
    // Highlight the quick-launch preset matching the current mode + arm so the
    // operator can see what they're about to enter.
    const curArm = BOTH_HANDS ? 'both' : ARM;
    document.querySelectorAll('.ql-btn').forEach((b) => {
        b.classList.toggle('active',
            b.dataset.mode === MODE && b.dataset.arm === curArm);
    });

    // Debug checkbox: append/strip &debug=1 on the quick-launch links so the
    // chosen preset enters with the debug overlay on (no URL typing).
    const dbg = document.getElementById('ql-debug');
    if (dbg) {
        dbg.checked = DEBUG_PARAM;
        const applyDebug = () => {
            document.querySelectorAll('.ql-btn').forEach((b) => {
                const u = new URL(b.href);
                if (dbg.checked) u.searchParams.set('debug', '1');
                else u.searchParams.delete('debug');
                b.setAttribute('href', '?' + u.searchParams.toString());
            });
        };
        dbg.addEventListener('change', applyDebug);
        applyDebug();
    }

    document.getElementById('arm-select').value = BOTH_HANDS ? 'both' : ARM;
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
// Per-band heatmap registry: one compact heatmap (+ IMU tilt widget) PER FlexGrid
// band, so two-hand mode shows BOTH bracelets side by side. Keyed by device_id.
// The panels are intentionally small -- they are "this band is alive + responding"
// feedback, not a panel to read closely. bandRow is their container (child of
// infoGroup) at the old single-heatmap origin.
let bandRow;
const bands = new Map();   // device_id -> {group, mesh, canvas, ctx, tex, imuMesh, vmax, lastSig}
const BAND_W = 0.17, BAND_H = 0.05;       // compact panel (~ FlexGrid 15:4 aspect)
const BAND_GAP = 0.03;
const BAND_ROLE_ORDER = { left: 0, right: 1, labeler: 2 };
// Reused temps for the per-band IMU accel-tilt (called sequentially each frame).
const _imuUp = new THREE.Vector3();
const _imuNormal = new THREE.Vector3(0, 1, 0);
const _imuTargetQuat = new THREE.Quaternion();
let headerCanvas, headerTex, headerMesh;
let statusMesh, statusCanvas, statusTex;
// Per-band battery + signal panel (one row per FlexGrid band + the labeler), so
// the operator sees both bracelets are alive + streaming at a glance.
let bandStatusMesh, bandStatusCanvas, bandStatusCtx, bandStatusTex;
let _bandStatusKey = '';
// Debug overlay: a larger panel surfacing the "is everything working" signals.
let debugMesh, debugCanvas, debugCtx, debugTex;
let _debugLastDraw = 0;
// Mutated in place each frame (no per-frame allocations on the hot path).
const debugState = { leftHand: false, rightHand: false, liveWs: false,
                     questWs: false, vis: '', snapAgeMs: 0 };
// Sync slate: big high-contrast splash that appears for SYNC_SLATE_MS ms
// at REC press. Visible in the headset's screen recording so the operator
// can frame-accurately pair the video to a CSV row when scrubbing later.
let slateMesh, slateCanvas, slateCtx, slateTex;
let slateShownUntil = 0;
const SYNC_SLATE_MS = 2500;
const SLATE_W = 0.60;
const SLATE_H = 0.20;
let compareMesh, compareCanvas, compareCtx, compareTex;
// Per-finger max wrist->tip distance ever observed, used as the "fully
// extended" reference so the curl normalization adapts to the user's hand
// size without explicit calibration. REAL and PREDICTED keep SEPARATE
// calibration arrays: the real distances are physical (~0.10 m), but the
// model's predicted distances can be wildly off-scale for a poorly-trained
// model (common early in training). If they shared one array, one bad
// predicted frame would pollute the max and distort the REAL bars too --
// exactly when you most need the real bars to stay trustworthy. Index,
// middle, ring, pinky.
const fingerMaxExtendedReal = [0.105, 0.110, 0.105, 0.090];
const fingerMaxExtendedPred = [0.105, 0.110, 0.105, 0.090];
let pinchIndicator;
let armGroup;                                  // captured-hand joint spheres (blue)
let armJointMeshes = new Map();                // joint-name -> sphere mesh
let offHandGroup;                              // off-hand joint spheres (green)
let offHandJointMeshes = new Map();            // joint-name -> sphere mesh
// Predicted-hand ghosts (amber spheres). One set PER SIDE so two-hand mode shows
// a ghost next to EACH real hand (driven by that hand's band prediction).
const ghosts = {};                             // side -> {group, meshes:Map<name,mesh>}
let placed = false;                            // anchors set on first XRFrame

// XR visibility state. Quest pauses the WebXR session whenever the user
// triggers system UI -- universal menu, notifications, or (the failure mode
// that motivated v1.11) walking out of the Guardian boundary and being
// prompted to redraw it. While paused, viewer/joint poses can return null
// or stale data, so we skip capture-and-send to avoid feeding garbage to
// the recorder. On resume we re-anchor panels since the user's spatial
// reference frame may have shifted.
let xrPaused = false;
let xrPausedAt = 0;        // timestamp of last pause start (for status display)

// Movable panel groups (v1.10). Heatmap + header + compare get wrapped in
// `infoGroup` so they move together as the "data display" cluster. The full
// menu lives in `menuRoot` along with the status strip so action UI moves
// as one unit. Each group gets a drag handle so the user can reposition it
// independently while the headset is on.
let infoGroup;
let menuRoot;

// SETUP config panel (replay section). A THREE.Group under menuRoot holding a
// title strip, N capture-list rows, a REPLAY/STOP action button, and a speed
// toggle. The list rows + action + speed are registered in `buttons[]` so the
// off-hand ray + pinch select them like any menu button (no new input system).
// configRowButtons holds the per-row button entries (name -> entry) so we can
// rebuild them when the capture list changes.
let configPanel;
let configTitleMesh, configTitleCanvas, configTitleCtx, configTitleTex;
const configRowButtons = [];   // capture-row button entries (in `buttons[]` too)

// BANDS section state. Each band slot is a wide info label (its own canvas
// mesh, NOT a button) plus three sub-button entries (L / R / clear, in
// buttons[]). configBandRows[i] = { info: {mesh,canvas,ctx,tex}, L, R, clear,
// device_id }. drawBandsSection() binds each slot to a discovered FlexGrid
// (or hides it) and paints. bandActivity tracks a per-device rolling baseline
// + recent activity so a physical squeeze briefly highlights the matching row
// (squeeze-to-identify). bandIdentify is the device_id currently lit (or null).
const configBandRows = [];
const bandActivity = new Map();   // device_id -> { baseline, last, until }
let bandIdentifyId = null;        // device_id flagged by the squeeze detector
const BAND_IDENTIFY_MS = 700;     // how long a squeeze keeps a row highlighted

// TRAIN + MODELS section state. configModelRows[i] = { role, info:{mesh,canvas,
// ctx,tex}, pick, _lastInfoKey }. Built in createConfigPanel below the REPLAY
// section: a row per side (left, right) with a model-name info label + a PICK
// sub-button that cycles the registry. The three TRAIN buttons live in buttons[]
// directly (TRAIN_BOTH / TRAIN_L / TRAIN_R).
const configModelRows = [];

// Collapsed STOP button shown in place of menuRoot while recording. Anchored
// to the same world position as menuRoot, but only one of the two is visible
// at any time (toggled by uiState.recording in updateFromSnapshot).
let stopButtonMesh, stopButtonCanvas, stopButtonCtx, stopButtonTex, stopButtonMat;

// Drag handle registry. Each entry = { handle: Mesh, target: Object3D, mat:
// Material }. The handle is rendered as a small cube; the target is the
// Group whose position the drag updates.
const dragHandles = [];

// Active drag state. dragTarget is the Group currently being moved; when
// non-null, updateDrag() repositions it each frame to follow dragController.
// grabOffset captures the (target - controller) position at grab time so
// the panel feels like it's being held at the exact point you pinched it,
// not snapping to the controller's center.
const dragState = {
    target: null,
    controller: null,
    grabOffset: new THREE.Vector3(),
    handleMat: null,        // material to color-flash while grabbed
    endAt: 0,               // when selectend (pinch release) was seen; 0 = held.
                            // A brief grace before actually ending the drag so a
                            // momentary hand-tracking pinch dropout doesn't drop
                            // the panel mid-move (a re-pinch within the grace
                            // cancels it). See DRAG_END_GRACE_MS.
};

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
let hoveredHandle = null;        // drag-handle entry the off-hand ray is over (mutually
                                 // exclusive with hoveredButton -- the ray hits one or
                                 // the other based on geometric closest-first)

// Hover hysteresis. Hand-tracking rays JITTER: the headset estimates hand pose
// from its cameras, so simply turning your HEAD wobbles the ray even with a
// still hand, sweeping the highlight across buttons. So a button only becomes
// hovered after the ray SETTLES on it for HOVER_DWELL_MS, and an existing hover
// is released only after the ray leaves all buttons for HOVER_RELEASE_MS. The
// visual ray line + click target stay responsive; only the highlight commit is
// debounced, which kills the "everything flickers when I look around" feel.
const HOVER_DWELL_MS = 90;
const HOVER_RELEASE_MS = 130;
let _hoverCandidate = null;       // button the ray is currently settling onto
let _hoverCandidateSince = 0;
let _hoverMissSince = 0;          // when the ray last left all buttons (0 = on one)

// Debounce the button highlight against ray jitter; updates hoveredButton.
function _settleHover(raw, now) {
    if (raw === hoveredButton) {            // steady on the current button
        _hoverCandidate = null;
        _hoverMissSince = 0;
        return;
    }
    if (raw === null) {                     // ray left all buttons
        _hoverCandidate = null;
        if (!_hoverMissSince) _hoverMissSince = now;
        if (now - _hoverMissSince >= HOVER_RELEASE_MS) hoveredButton = null;
        return;
    }
    _hoverMissSince = 0;                     // a different button is under the ray
    if (raw !== _hoverCandidate) {
        _hoverCandidate = raw;
        _hoverCandidateSince = now;
    } else if (now - _hoverCandidateSince >= HOVER_DWELL_MS) {
        hoveredButton = raw;                 // settled long enough -> commit
        _hoverCandidate = null;
    }
}

// Server-derived state (mirrored into button visuals each frame from /ws/live)
const uiState = {
    recording: false,
    debugOn: DEBUG_PARAM,          // debug overlay visible (?debug=1 or DEBUG button)
    sessionActive: false,
    sessionId: null,
    training: false,
    inferenceLoaded: false,        // a model is loaded on the server
    inferenceEnabled: false,       // and predictions are actively flowing
    inferenceModel: null,          // model name string for status
    // pinch is the hands-free fallback for REC only
    pinchStart: 0,
    pinchToggled: false,   // already fired a toggle for the current hold; needs release
};

let lastReportAt = 0;
let lastStatusAt = 0;
let lastStatusText = '';

// SETUP config panel state. Drives the REPLAY section: the cached capture
// list (from GET /api/captures), which capture row is selected, the replay
// speed toggle, and whether the panel is open. The per-frame replay progress
// + STOP/REPLAY button state are read live from latestSnapshot.replay (driven
// in drawReplaySection), not stored here.
const configState = {
    open: false,
    captures: [],          // [{name, rows, size_bytes, ...}] cached on open
    selected: null,        // chosen capture name (string) or null
    speed: 1.0,            // replay speed sent to POST /api/simulate/replay
    loading: false,        // a GET /api/captures fetch is in flight
    // TRAIN + MODELS section state.
    models: [],            // [{name, path, created, metrics, active}] from GET /api/models
    modelLeft: null,       // currently-loaded model name for the left slot (or null)
    modelRight: null,      // currently-loaded model name for the right slot (or null)
    modelIdx: { left: -1, right: -1 },   // cycle cursor into models[] per side
    trainBusy: false,      // a TRAIN POST sequence is in flight (drives the spinner)
    trainResult: '',       // last train summary line ("L R²=0.91 / R R²=0.88")
};

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

    // Info group (data display cluster): heatmap + header + compare. Wrapped
    // in a single Group so the user can drag-move them all together via the
    // info-group handle. Child positions are local-space offsets from the
    // group origin (which is at the heatmap center).
    infoGroup = new THREE.Group();
    scene.add(infoGroup);

    // Band row: per-FlexGrid heatmaps (+ IMU tilt) are created lazily into this
    // container as bands appear (reconcileBands), centered at the old single-
    // heatmap origin. Two-hand mode fills it with LEFT + RIGHT side by side.
    bandRow = new THREE.Group();
    infoGroup.add(bandRow);
    // Namespaced debug handle (cf. desktop window.OMImuViewer): lets tooling drive
    // the band visuals without an XR session + inspect band state.
    window.OMVR = { bands, getBands, reconcileBands, getStatusDevices, drawBandStatus,
                    drawDebug, debugState, uiState, predForSide, ghosts,
                    drawOrientationGizmo, updateBandImu, THREE,
                    bandStatusCtx: () => bandStatusCtx, debugCtx: () => debugCtx,
                    // SETUP / REPLAY panel handles for headless preview driving
                    // (no XR session needed): open/close the panel, inject a
                    // fake capture list, pick a capture, and render the replay
                    // section given a fake snapshot. configPanel is the group.
                    configState,
                    get configPanel() { return configPanel; },
                    openSetup, closeSetup, toggleSetup,
                    setCaptures, selectCapture, fetchCaptures, drawReplaySection,
                    // BANDS section handles: inject a fake devices array + render
                    // the band rows headless (setDevices), or paint from an
                    // explicit snapshot (drawBandsSection). setBandRole posts a
                    // tag; updateBandIdentify drives the squeeze-to-identify pass.
                    setDevices, drawBandsSection, setBandRole,
                    bandActivityScalar, updateBandIdentify,
                    get configBandRows() { return configBandRows; },
                    get bandIdentifyId() { return bandIdentifyId; },
                    // TRAIN + MODELS section handles: train both hands (or one
                    // via trainBoth('left'|'right')), inject a fake model list +
                    // render the rows headless (setModels), load a model into a
                    // per-hand slot, and paint the section from a fake snapshot.
                    trainBoth, trainOneRole, pickTrainCaptures,
                    setModels, fetchModels, loadModelForRole, onModelPick,
                    drawTrainSection, modelNameFromPath,
                    get configModelRows() { return configModelRows; } };

    // Header strip above the heatmap (status text rendered on its own canvas)
    headerCanvas = document.createElement('canvas');
    headerCanvas.width = 600; headerCanvas.height = 90;
    headerTex = new THREE.CanvasTexture(headerCanvas);
    headerTex.colorSpace = THREE.SRGBColorSpace;
    headerMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(HEATMAP_W, HEATMAP_H * 0.5),
        new THREE.MeshBasicMaterial({ map: headerTex, transparent: true }));
    headerMesh.position.y = HEATMAP_H * 0.6;   // local-space offset above heatmap
    infoGroup.add(headerMesh);
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
    // Row 1: actions + setup (SETUP toggles the config/replay panel)
    createMenuButton('TRAIN',   'TRAIN',    () => uiState.training,    runTrain,    1, 0);
    createMenuButton('SETUP',   'SETUP',    () => configState.open,    toggleSetup, 1, 1);
    createMenuButton('RECENTER','RECENTER', () => false,               recenterUI,  1, 2);
    // Row 2: system
    createMenuButton('EXIT',    'EXIT VR',  () => false,               exitVR,      2, 0);

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
            // If this controller was mid-drag when it dropped out, release
            // the panel so it doesn't get orphaned in the GRAB state.
            if (dragState.controller === ctrl) cancelDrag();
        });
        ctrl.addEventListener('selectstart', () => onControllerSelect(ctrl));
        // selectend fires when pinch releases -- only used to end an active
        // drag. Button activations are instantaneous (no hold required), so
        // we don't need to track selectend for them. Don't end IMMEDIATELY:
        // hand tracking drops the pinch for a frame or two while you move,
        // which used to drop the panel mid-drag. Mark the time and let the
        // frame loop end it after DRAG_END_GRACE_MS unless the pinch returns.
        ctrl.addEventListener('selectend', () => {
            if (dragState.controller === ctrl && !dragState.endAt) {
                dragState.endAt = performance.now();
            }
        });
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

    // REAL vs PRED comparison panel. Local to infoGroup so it moves with the
    // data display when the user drags it. Hidden until inference is running
    // AND a captured hand is being tracked (otherwise nothing to compare).
    compareCanvas = document.createElement('canvas');
    compareCanvas.width = 800; compareCanvas.height = 100;
    compareCtx = compareCanvas.getContext('2d');
    compareTex = new THREE.CanvasTexture(compareCanvas);
    compareTex.colorSpace = THREE.SRGBColorSpace;
    compareMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(COMPARE_W, COMPARE_H),
        new THREE.MeshBasicMaterial({ map: compareTex, transparent: true }));
    compareMesh.visible = false;
    compareMesh.position.y = -COMPARE_OFFSET_DOWN;   // local offset below heatmap
    compareMesh.rotation.x = THREE.MathUtils.degToRad(MENU_TILT_DEG);
    infoGroup.add(compareMesh);
    drawCompare(null, null);

    // Menu root (action UI cluster): menuPanel + statusMesh wrapped in a
    // group so the user can drag-move the whole action area as a unit. The
    // menuPanel itself stays as its own group (already has button children).
    menuRoot = new THREE.Group();
    scene.add(menuRoot);
    menuRoot.add(menuPanel);
    menuPanel.position.set(0, 0, 0);   // local origin = menuRoot origin

    // Status strip below the menu -- now a child of menuRoot so it moves
    // along when the menu is dragged.
    statusCanvas = document.createElement('canvas');
    statusCanvas.width = 800; statusCanvas.height = 90;
    statusTex = new THREE.CanvasTexture(statusCanvas);
    statusTex.colorSpace = THREE.SRGBColorSpace;
    statusMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(STATUS_W, STATUS_H),
        new THREE.MeshBasicMaterial({ map: statusTex, transparent: true }));
    statusMesh.position.y = -(STATUS_ROW_DOWN - MENU_OFFSET_DOWN);  // gap menu->status
    menuRoot.add(statusMesh);
    drawStatus('');

    // Per-band battery + signal panel, just below the status strip. Child of
    // menuRoot so it inherits the drag/tilt/AR-scale of the menu cluster.
    bandStatusCanvas = document.createElement('canvas');
    bandStatusCanvas.width = 800; bandStatusCanvas.height = 200;
    bandStatusCtx = bandStatusCanvas.getContext('2d');
    bandStatusTex = new THREE.CanvasTexture(bandStatusCanvas);
    bandStatusTex.colorSpace = THREE.SRGBColorSpace;
    bandStatusMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(BANDSTATUS_W, BANDSTATUS_H),
        new THREE.MeshBasicMaterial({ map: bandStatusTex, transparent: true }));
    bandStatusMesh.position.y = statusMesh.position.y
        - STATUS_H / 2 - BANDSTATUS_H / 2 - 0.008;
    menuRoot.add(bandStatusMesh);

    // Debug overlay panel below the band-status rows, shown only in debug mode.
    debugCanvas = document.createElement('canvas');
    debugCanvas.width = 720; debugCanvas.height = 600;
    debugCtx = debugCanvas.getContext('2d');
    debugTex = new THREE.CanvasTexture(debugCanvas);
    debugTex.colorSpace = THREE.SRGBColorSpace;
    debugMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(0.34, 0.283),
        new THREE.MeshBasicMaterial({ map: debugTex, transparent: true }));
    debugMesh.position.set(0,
        bandStatusMesh.position.y - BANDSTATUS_H / 2 - 0.152, 0.001);
    debugMesh.visible = uiState.debugOn;
    menuRoot.add(debugMesh);

    // Collapsed STOP button: shown in place of the full menu while
    // recording. Anchored to menuRoot too so it lives at the same world
    // position; we toggle .visible based on uiState.recording each frame
    // (in updateFromSnapshot / a small helper).
    stopButtonCanvas = document.createElement('canvas');
    stopButtonCanvas.width = 512; stopButtonCanvas.height = 280;
    stopButtonCtx = stopButtonCanvas.getContext('2d');
    stopButtonTex = new THREE.CanvasTexture(stopButtonCanvas);
    stopButtonTex.colorSpace = THREE.SRGBColorSpace;
    stopButtonMat = new THREE.MeshBasicMaterial({
        map: stopButtonTex, transparent: true,
        side: THREE.DoubleSide, depthWrite: false,
    });
    stopButtonMesh = new THREE.Mesh(new THREE.PlaneGeometry(STOP_W, STOP_H),
                                     stopButtonMat);
    stopButtonMesh.userData.buttonName = 'STOP_COLLAPSED';   // raycast target
    stopButtonMesh.visible = false;
    menuRoot.add(stopButtonMesh);
    drawStopButton(false);

    // Drag handles -- one for infoGroup, one for menuRoot. Position each
    // handle in the top-left corner of its target so it's easy to spot
    // without obscuring the panel content.
    createDragHandle(infoGroup,
                     new THREE.Vector3(-HEATMAP_W / 2 - HANDLE_SIZE_M,
                                        HEATMAP_H / 2 + HANDLE_SIZE_M, 0));
    createDragHandle(menuRoot,
                     new THREE.Vector3(-MENU_W / 2 - HANDLE_SIZE_M,
                                        MENU_H / 2 + HANDLE_SIZE_M, 0));

    // SETUP config panel (replay-from-headset). Built here so it exists for
    // headless preview (window.OMVR.openSetup()) without an XR session. Hidden
    // until the SETUP button toggles it. Anchored under menuRoot so it
    // inherits the menu cluster's drag/tilt/AR-scale.
    createConfigPanel();

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

    // AR-mode scaling is now applied to infoGroup + menuRoot directly in
    // placeAnchors (single source of truth). Individual children inherit
    // their parent group's scale.

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
    const makeGhost = () => {
        const group = new THREE.Group();
        scene.add(group);
        const meshes = new Map();
        for (const name of JOINT_NAMES) {
            const m = new THREE.Mesh(ghostJointGeo, ghostJointMat);
            m.visible = false;
            group.add(m);
            meshes.set(name, m);
        }
        return { group, meshes };
    };
    ghosts.left = makeGhost();
    ghosts.right = makeGhost();

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
    // Config-panel buttons (capture rows, REPLAY action, speed toggle) draw
    // their own canvas via a customDraw hook so they can show capture names,
    // progress lines, selection highlight etc. They still live in `buttons[]`
    // so the existing raycast + pinch + cooldown machinery targets them with
    // no new input system. customDraw owns the redraw-skip decision itself.
    if (btn.customDraw) { btn.customDraw(btn, hovered); return; }
    const active = !!btn.isActive();
    const flashing = (btn.flashUntil || 0) > performance.now();
    // Skip the redraw + GPU texture re-upload when nothing about this button's
    // appearance changed. updateButtonVisual calls this for all 6 buttons every
    // frame, but they only change on active/hover/flash transitions -- without
    // this guard that's 6 texture uploads/frame at 72fps for nothing. (Same
    // fix v1.14 applied to the STOP button + status strip; the menu buttons
    // were missed.) The flashing bool is time-derived, so a flash naturally
    // forces one more redraw when it expires (true -> false flips the key).
    const labelText = (btn.name === 'REC' && active) ? 'STOP' : btn.label;
    const drawKey = `${active}|${hovered}|${flashing}|${labelText}`;
    if (btn._lastDrawKey === drawKey) return;
    btn._lastDrawKey = drawKey;

    const ctx = btn.ctx;
    const W = btn.canvas.width, H = btn.canvas.height;
    ctx.clearRect(0, 0, W, H);

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
    // (labelText computed above for the redraw-skip key).
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

// ---------------------------------------------------------------------------
// SETUP config panel + REPLAY section (replay a past capture from the headset)
//
// A dedicated THREE.Group (configPanel) anchored under menuRoot, built like
// the menu cluster (a dark plate + canvas-mesh children) with its own drag
// handle. Toggled by the SETUP menu button. The REPLAY section is a list of
// past captures (GET /api/captures) rendered as canvas-drawn rows; each row
// is registered in `buttons[]` as a raycast target so the off-hand ray +
// pinch selects it exactly like a menu button. A REPLAY action button POSTs
// /api/simulate/replay; while a replay is active it becomes STOP REPLAY and a
// progress line shows "replaying <capture> N/T". A speed toggle cycles 1x/2x.
// All interaction reuses onControllerSelect + BUTTON_COOLDOWN_MS -- no new
// input system. The panel is built at initScene so window.OMVR can drive it
// headless (no XR session) for preview verification.
// ---------------------------------------------------------------------------

// Shared rounded-rect path helper (same geometry the menu/STOP buttons inline).
function _roundRectPath(ctx, W, H, r) {
    ctx.beginPath();
    ctx.moveTo(r, 0);
    ctx.lineTo(W - r, 0); ctx.arcTo(W, 0, W, r, r);
    ctx.lineTo(W, H - r); ctx.arcTo(W, H, W - r, H, r);
    ctx.lineTo(r, H);     ctx.arcTo(0, H, 0, H - r, r);
    ctx.lineTo(0, r);     ctx.arcTo(0, 0, r, 0, r);
    ctx.closePath();
}

// Create a config-panel button: a PlaneGeometry + canvas-texture mesh parented
// to configPanel, registered in `buttons[]` (so the existing raycast/pinch/
// cooldown path targets it) with a customDraw hook that drawMenuButton
// delegates to. Returns the button entry.
function createConfigButton(name, w, h, localPos, onActivate, customDraw) {
    const canvas = document.createElement('canvas');
    canvas.width = 512; canvas.height = 80;
    const ctx = canvas.getContext('2d');
    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    const mat = new THREE.MeshBasicMaterial({
        map: tex, transparent: true, side: THREE.DoubleSide, depthWrite: false,
    });
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
    mesh.position.copy(localPos);
    mesh.userData.buttonName = name;   // raycaster looks this up
    configPanel.add(mesh);
    const btn = {
        name, label: name, mesh, canvas, ctx, tex, mat,
        isActive: () => false, onActivate, lastActivateAt: 0,
        customDraw, _lastDrawKey: null,
        isConfig: true,   // gated by configPanel.visible in updateRaycast
    };
    buttons[name] = btn;
    return btn;
}

// Capture-row paint: capture name + row count, highlighted when it's the
// chosen capture (configState.selected) or hovered by the off-hand ray.
function drawCaptureRow(btn, hovered) {
    const cap = btn.userData_capture;   // {name, rows} or null (empty-list row)
    const selected = cap && configState.selected === cap.name;
    const drawKey = `${hovered}|${selected}|${cap ? cap.name : ''}|${cap ? cap.rows : ''}`;
    if (btn._lastDrawKey === drawKey) return;
    btn._lastDrawKey = drawKey;
    const ctx = btn.ctx;
    const W = btn.canvas.width, H = btn.canvas.height;
    ctx.clearRect(0, 0, W, H);
    let bg;
    if (selected)     bg = '#15803d';   // emerald: chosen capture
    else if (hovered) bg = '#1f6feb';
    else              bg = '#2a3142';
    ctx.fillStyle = bg;
    _roundRectPath(ctx, W, H, 16);
    ctx.fill();
    ctx.fillStyle = '#f0f4f8';
    ctx.textBaseline = 'middle';
    if (!cap) {
        // Empty-list placeholder row (not selectable in practice).
        ctx.font = '30px system-ui';
        ctx.textAlign = 'center';
        ctx.fillText('no captures yet', W / 2, H / 2);
    } else {
        // Name left-aligned (trimmed), row count right-aligned.
        ctx.font = '30px system-ui';
        ctx.textAlign = 'left';
        const name = cap.name.length > 28 ? cap.name.slice(0, 27) + '…' : cap.name;
        ctx.fillText(name, 18, H / 2);
        ctx.textAlign = 'right';
        ctx.fillStyle = '#9fb3c8';
        const rows = (typeof cap.rows === 'number') ? `${cap.rows} rows` : '';
        ctx.fillText(rows, W - 18, H / 2);
    }
    btn.tex.needsUpdate = true;
}

// REPLAY / STOP REPLAY action button paint. Driven by latestSnapshot.replay
// (active state) each frame so it flips to STOP REPLAY while a replay runs.
function drawReplayAction(btn, hovered) {
    const snap = latestSnapshot;
    const rp = (snap && snap.replay) || { active: false };
    const flashing = (btn.flashUntil || 0) > performance.now();
    const label = rp.active ? 'STOP REPLAY' : 'REPLAY';
    const ready = !!configState.selected || rp.active;   // need a pick to start
    const drawKey = `${hovered}|${flashing}|${label}|${ready}`;
    if (btn._lastDrawKey === drawKey) return;
    btn._lastDrawKey = drawKey;
    const ctx = btn.ctx;
    const W = btn.canvas.width, H = btn.canvas.height;
    ctx.clearRect(0, 0, W, H);
    let bg;
    if (flashing)        bg = '#ffffff';
    else if (rp.active)  bg = '#ef4444';   // red while replaying (tap = stop)
    else if (!ready)     bg = '#374151';   // muted: no capture selected yet
    else if (hovered)    bg = '#1f6feb';
    else                 bg = '#2563eb';
    ctx.fillStyle = bg;
    _roundRectPath(ctx, W, H, 16);
    ctx.fill();
    ctx.fillStyle = flashing ? '#0d1117' : '#f0f4f8';
    ctx.font = 'bold 34px system-ui';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, W / 2, H / 2);
    btn.tex.needsUpdate = true;
}

// Speed toggle paint: shows the current replay speed (1x / 2x).
function drawSpeedToggle(btn, hovered) {
    const flashing = (btn.flashUntil || 0) > performance.now();
    const label = `SPEED ${configState.speed % 1 === 0
        ? configState.speed.toFixed(0) : configState.speed}x`;
    const drawKey = `${hovered}|${flashing}|${label}`;
    if (btn._lastDrawKey === drawKey) return;
    btn._lastDrawKey = drawKey;
    const ctx = btn.ctx;
    const W = btn.canvas.width, H = btn.canvas.height;
    ctx.clearRect(0, 0, W, H);
    let bg;
    if (flashing)     bg = '#ffffff';
    else if (hovered) bg = '#1f6feb';
    else              bg = '#2a3142';
    ctx.fillStyle = bg;
    _roundRectPath(ctx, W, H, 16);
    ctx.fill();
    ctx.fillStyle = flashing ? '#0d1117' : '#f0f4f8';
    ctx.font = 'bold 32px system-ui';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, W / 2, H / 2);
    btn.tex.needsUpdate = true;
}

// Title strip paint: "SETUP · REPLAY" plus the live progress line when a
// replay is active ("replaying <capture> N/T"). Always redrawn (cheap, one
// strip) because the progress counter changes every frame during replay.
function drawConfigTitle() {
    const snap = latestSnapshot;
    const rp = (snap && snap.replay) || { active: false };
    const ctx = configTitleCtx;
    const W = configTitleCanvas.width, H = configTitleCanvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = 'rgba(13, 17, 23, 0.85)';
    _roundRectPath(ctx, W, H, 14);
    ctx.fill();
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    ctx.fillStyle = '#f0f4f8';
    ctx.font = 'bold 34px system-ui';
    ctx.fillText('SETUP · BANDS · REPLAY · TRAIN', 20, H * 0.3);
    // Progress / hint line.
    ctx.font = '28px system-ui';
    if (rp.active) {
        ctx.fillStyle = '#34d399';
        const cap = rp.capture || configState.selected || '';
        const capShort = cap.length > 22 ? cap.slice(0, 21) + '…' : cap;
        ctx.fillText(`replaying ${capShort}  ${rp.frame || 0}/${rp.total || 0}`,
                     20, H * 0.72);
    } else {
        ctx.fillStyle = '#9fb3c8';
        const sel = configState.selected
            ? `selected ${configState.selected.length > 22
                ? configState.selected.slice(0, 21) + '…' : configState.selected}`
            : (configState.loading ? 'loading captures…' : 'pick a capture below');
        ctx.fillText(sel, 20, H * 0.72);
    }
    configTitleTex.needsUpdate = true;
}

// ---------------------------------------------------------------------------
// BANDS section (in-VR band L/R tagging)
//
// One row per discovered FlexGrid band. The wide info label shows the short
// device_id (last 6 chars), the current role badge (LEFT / RIGHT / --), and a
// battery%/Hz tag if present. Three sub-buttons (L | R | clear) post to
// /api/discovery/role; the NEXT snapshot's device.role confirms the tag and is
// reflected in the badge + the active-button highlight. A squeeze-to-identify
// detector watches each band's matrix activity and briefly lights the row whose
// physical band the user squeezes, so they tag the right one. All interaction
// reuses createConfigButton + the buttons[] raycast/pinch path.
// ---------------------------------------------------------------------------

// Per-flexgrid activity scalar from its matrix (max cell). Robust to a missing
// or empty matrix (returns 0). matrix is [cols][rows] of numbers.
function bandActivityScalar(dev) {
    const m = dev && dev.matrix;
    if (!Array.isArray(m) || m.length === 0) return 0;
    let max = 0;
    for (const col of m) {
        if (!Array.isArray(col)) continue;
        for (const v of col) {
            const n = typeof v === 'number' ? v : 0;
            if (n > max) max = n;
        }
    }
    return max;
}

// Squeeze-to-identify: track a slow rolling baseline per device_id and flag the
// ONE band whose activity jumps well above its own baseline AND above the other
// bands' current activity. The flagged id stays lit for BAND_IDENTIFY_MS so a
// brief squeeze produces a visible, stable highlight. Called each frame with
// the live snapshot. Keeps state in bandActivity; sets bandIdentifyId.
function updateBandIdentify(snap) {
    const now = performance.now();
    const fgs = (snap && Array.isArray(snap.devices) ? snap.devices : [])
        .filter((d) => d && d.device_type === 'flexgrid');
    let best = null, bestExcess = 0;
    for (const d of fgs) {
        const act = bandActivityScalar(d);
        let rec = bandActivity.get(d.device_id);
        if (!rec) { rec = { baseline: act, last: act, until: 0 }; bandActivity.set(d.device_id, rec); }
        // Slow EMA baseline so a sustained squeeze still reads as "above" it;
        // a quick (0.08) follow on the last value powers the spike compare.
        rec.baseline = rec.baseline * 0.97 + act * 0.03;
        rec.last = act;
        // Excess of this band over its own baseline. Require a real jump
        // (15% over baseline + a small absolute floor) so idle noise doesn't fire.
        const excess = act - rec.baseline * 1.15;
        if (excess > 8 && excess > bestExcess) { bestExcess = excess; best = d; }
    }
    if (best) {
        // Confirm it also leads the other bands' current activity (so squeezing
        // one band doesn't light another that happens to be drifting up).
        const bestAct = bandActivityScalar(best);
        let leads = true;
        for (const d of fgs) {
            if (d.device_id === best.device_id) continue;
            if (bandActivityScalar(d) >= bestAct) { leads = false; break; }
        }
        if (leads) {
            const rec = bandActivity.get(best.device_id);
            rec.until = now + BAND_IDENTIFY_MS;
            bandIdentifyId = best.device_id;
        }
    }
    // Expire the highlight once its window passes.
    if (bandIdentifyId) {
        const rec = bandActivity.get(bandIdentifyId);
        if (!rec || rec.until < now) bandIdentifyId = null;
    }
}

// POST a role tag for a band. role in {'left','right','labeler',''}. The next
// snapshot's device.role confirms it (we don't optimistically mutate state);
// surface success / the server's detail in the status strip. 400 on unknown
// device or invalid role.
async function setBandRole(deviceId, role) {
    try {
        const r = await fetch('/api/discovery/role', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: deviceId, role }),
        });
        if (r.ok) {
            const short = (deviceId || '').slice(-6);
            setStatus(role ? `tagged ${short} = ${role}` : `cleared ${short}`);
        } else {
            let detail = `HTTP ${r.status}`;
            try { const err = await r.json(); if (err && err.detail) detail = err.detail; }
            catch (e) { /* non-JSON error body */ }
            setStatus(`role failed: ${detail}`.slice(0, 80));
        }
    } catch (e) {
        setStatus(`role error: ${e.message}`);
    }
}

// Band info-label paint: short device_id + role badge + batt%/Hz tag. Lights up
// (amber outline) while the squeeze detector has this band flagged so the user
// can see which physical band they're squeezing maps to which row.
function drawBandInfoLabel(row, dev) {
    const ctx = row.info.ctx;
    const W = row.info.canvas.width, H = row.info.canvas.height;
    const role = (dev && dev.role) || '';
    const short = dev ? (dev.device_id || '').slice(-6) : '';
    const s = (dev && dev.status) || {};
    const pct = (s.pct != null) ? `${s.pct}%` : '';
    const hz = (dev && dev.hz) ? `${Math.round(dev.hz)}Hz` : '';
    const tag = [pct, hz].filter(Boolean).join(' ');
    const identify = dev && bandIdentifyId === dev.device_id;
    const drawKey = `${short}|${role}|${tag}|${identify}`;
    if (row._lastInfoKey === drawKey) return;
    row._lastInfoKey = drawKey;
    ctx.clearRect(0, 0, W, H);
    // Background; amber-flooded while squeeze-identified.
    ctx.fillStyle = identify ? '#7c5e12' : '#2a3142';
    _roundRectPath(ctx, W, H, 16);
    ctx.fill();
    if (!dev) { row.info.tex.needsUpdate = true; return; }
    // device_id (last 6) left-aligned.
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    ctx.fillStyle = '#f0f4f8';
    ctx.font = 'bold 30px system-ui';
    ctx.fillText(short, 16, H * 0.34);
    // Role badge under the id: LEFT (red) / RIGHT (blue) / -- (gray).
    let badge = '--', badgeColor = '#9fb3c8';
    if (role === 'left')  { badge = 'LEFT';  badgeColor = '#ef4444'; }
    else if (role === 'right') { badge = 'RIGHT'; badgeColor = '#1f6feb'; }
    else if (role) { badge = role.toUpperCase(); badgeColor = '#9fb3c8'; }
    ctx.font = 'bold 26px system-ui';
    ctx.fillStyle = badgeColor;
    ctx.fillText(badge, 16, H * 0.74);
    // Battery%/Hz tag right-aligned.
    if (tag) {
        ctx.textAlign = 'right';
        ctx.fillStyle = '#9fb3c8';
        ctx.font = '24px system-ui';
        ctx.fillText(tag, W - 16, H * 0.74);
    }
    row.info.tex.needsUpdate = true;
}

// Role sub-button (L / R / clear) paint. customDraw hook. Highlights when its
// role is the band's ACTIVE role (red for L, blue for R, gray-bright for clear),
// or blue while hovered, else muted. Reads the bound row's current device role.
function drawBandRoleButton(btn, hovered) {
    const row = btn.userData_bandRow;
    const dev = row && row.device;
    const role = btn.userData_role;            // 'left' | 'right' | ''
    const active = !!dev && ((dev.role || '') === role);
    const flashing = (btn.flashUntil || 0) > performance.now();
    const enabled = !!dev;                     // no bound band -> muted, inert
    const label = role === 'left' ? 'L' : role === 'right' ? 'R' : 'x';
    const drawKey = `${hovered}|${active}|${flashing}|${enabled}|${label}`;
    if (btn._lastDrawKey === drawKey) return;
    btn._lastDrawKey = drawKey;
    const ctx = btn.ctx;
    const W = btn.canvas.width, H = btn.canvas.height;
    ctx.clearRect(0, 0, W, H);
    let bg;
    if (flashing)            bg = '#ffffff';
    else if (!enabled)       bg = '#374151';   // muted: no band bound
    else if (active && role === 'left')  bg = '#ef4444';   // red = LEFT active
    else if (active && role === 'right') bg = '#1f6feb';   // blue = RIGHT active
    else if (active)         bg = '#6b7280';   // gray = clear/other active
    else if (hovered)        bg = '#1f6feb';
    else                     bg = '#2a3142';
    ctx.fillStyle = bg;
    _roundRectPath(ctx, W, H, 12);
    ctx.fill();
    ctx.fillStyle = flashing ? '#0d1117' : '#f0f4f8';
    ctx.font = 'bold 34px system-ui';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, W / 2, H / 2);
    btn.tex.needsUpdate = true;
}

// L / R / clear sub-button activation: tag the bound band's role. No-op if the
// slot has no band bound (hidden rows are non-hoverable anyway).
function onBandRoleSelect(rowIndex, role) {
    const row = configBandRows[rowIndex];
    const dev = row && row.device;
    if (!dev || !dev.device_id) return;
    setBandRole(dev.device_id, role);
}

// Bind discovered FlexGrid bands onto the fixed band slots and paint them.
// Filters snap.devices to flexgrids (NOT just streaming ones -- a band with no
// matrix yet can still be tagged). Extra slots (beyond the band count) are
// hidden. Used live each frame and headless via window.OMVR.drawBandsSection.
function drawBandsSection(snap) {
    const fgs = (snap && Array.isArray(snap.devices) ? snap.devices : [])
        .filter((d) => d && d.device_type === 'flexgrid')
        .sort((a, b) =>
            ((BAND_ROLE_ORDER[a.role] ?? 9) - (BAND_ROLE_ORDER[b.role] ?? 9))
            || (a.device_id < b.device_id ? -1 : 1));
    for (let i = 0; i < configBandRows.length; i++) {
        const row = configBandRows[i];
        const dev = i < fgs.length ? fgs[i] : null;
        row.device = dev;
        const vis = !!dev;
        row.info.mesh.visible = vis;
        row.L.mesh.visible = vis;
        row.R.mesh.visible = vis;
        row.clear.mesh.visible = vis;
        if (vis) {
            drawBandInfoLabel(row, dev);
            drawBandRoleButton(row.L, row.L === hoveredButton);
            drawBandRoleButton(row.R, row.R === hoveredButton);
            drawBandRoleButton(row.clear, row.clear === hoveredButton);
        }
    }
}

// Headless driver: inject a fake devices array, run the squeeze detector, and
// render the BANDS rows -- no XR session or /ws/live needed (for preview
// verification). Wraps the list in a minimal snapshot so the same code path
// runs as live. Returns the bound rows for inspection.
function setDevices(list) {
    const snap = { devices: Array.isArray(list) ? list : [] };
    updateBandIdentify(snap);
    drawBandsSection(snap);
    return configBandRows;
}

// Build the config panel group, its title strip, the (rebuildable) capture-row
// buttons, the REPLAY action button, and the speed toggle. Hidden until open.
function createConfigPanel() {
    configPanel = new THREE.Group();
    // Translucent plate behind everything so the section reads as one surface.
    const plate = new THREE.Mesh(
        new THREE.PlaneGeometry(CONFIG_W, CONFIG_H),
        new THREE.MeshBasicMaterial({
            color: 0x0d1117, transparent: true, opacity: 0.6,
            side: THREE.DoubleSide,
        }));
    plate.position.z = -0.001;
    configPanel.add(plate);

    // Layout: lay children top-to-bottom from the panel top. yTop is the local
    // y of the top inner edge; we step down by each element's height + gap.
    const topY = CONFIG_H / 2 - CONFIG_PAD;

    // Title strip.
    configTitleCanvas = document.createElement('canvas');
    configTitleCanvas.width = 640; configTitleCanvas.height = 120;
    configTitleCtx = configTitleCanvas.getContext('2d');
    configTitleTex = new THREE.CanvasTexture(configTitleCanvas);
    configTitleTex.colorSpace = THREE.SRGBColorSpace;
    configTitleMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(CONFIG_ROW_W, CONFIG_TITLE_H),
        new THREE.MeshBasicMaterial({ map: configTitleTex, transparent: true,
                                       depthWrite: false }));
    configTitleMesh.position.set(0, topY - CONFIG_TITLE_H / 2, 0.001);
    configPanel.add(configTitleMesh);

    // --- BANDS section (above REPLAY) ---------------------------------------
    let by = topY - CONFIG_TITLE_H - CONFIG_ROW_GAP;

    // Static "BANDS -- tag L / R" section header (a plain canvas label, not a
    // button). Drawn once here; it never changes.
    const bandsHdrCanvas = document.createElement('canvas');
    bandsHdrCanvas.width = 512; bandsHdrCanvas.height = 80;
    const bhc = bandsHdrCanvas.getContext('2d');
    bhc.fillStyle = '#9fb3c8';
    bhc.font = 'bold 30px system-ui';
    bhc.textBaseline = 'middle';
    bhc.textAlign = 'left';
    bhc.fillText('BANDS · tag L / R / clear', 8, bandsHdrCanvas.height / 2);
    const bandsHdrTex = new THREE.CanvasTexture(bandsHdrCanvas);
    bandsHdrTex.colorSpace = THREE.SRGBColorSpace;
    const bandsHdrMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(CONFIG_ROW_W, CONFIG_ROW_H),
        new THREE.MeshBasicMaterial({ map: bandsHdrTex, transparent: true,
                                       depthWrite: false }));
    bandsHdrMesh.position.set(0, by - CONFIG_ROW_H / 2, 0.001);
    configPanel.add(bandsHdrMesh);
    by -= CONFIG_ROW_H + CONFIG_ROW_GAP;

    // Band slots. Each row = a wide info label (canvas mesh) on the left + three
    // sub-buttons (L | R | clear) on the right. The three sub-buttons share the
    // row's vertical center; the info label fills the remaining width. x=0 is the
    // row center (CONFIG_ROW_W wide), so the left edge is -CONFIG_ROW_W/2.
    const rowLeft = -CONFIG_ROW_W / 2;
    for (let i = 0; i < CONFIG_BAND_ROWS; i++) {
        const rowCY = by - CONFIG_ROW_H / 2;

        // Wide info label (NOT a button -- raycast never targets it).
        const infoCanvas = document.createElement('canvas');
        infoCanvas.width = 512; infoCanvas.height = 80;
        const infoCtx = infoCanvas.getContext('2d');
        const infoTex = new THREE.CanvasTexture(infoCanvas);
        infoTex.colorSpace = THREE.SRGBColorSpace;
        const infoMesh = new THREE.Mesh(
            new THREE.PlaneGeometry(CONFIG_BAND_INFO_W, CONFIG_ROW_H),
            new THREE.MeshBasicMaterial({ map: infoTex, transparent: true,
                                           depthWrite: false }));
        infoMesh.position.set(rowLeft + CONFIG_BAND_INFO_W / 2, rowCY, 0.001);
        configPanel.add(infoMesh);

        // Three sub-buttons to the right of the info label.
        const btnStartX = rowLeft + CONFIG_BAND_INFO_W + CONFIG_BAND_BTN_GAP
            + CONFIG_BAND_BTN_W / 2;
        const step = CONFIG_BAND_BTN_W + CONFIG_BAND_BTN_GAP;
        const row = {
            info: { mesh: infoMesh, canvas: infoCanvas, ctx: infoCtx, tex: infoTex },
            device: null, _lastInfoKey: null,
        };
        const mkBtn = (suffix, role, slot) => {
            const b = createConfigButton(
                `BANDROW_${i}_${suffix}`, CONFIG_BAND_BTN_W, CONFIG_ROW_H,
                new THREE.Vector3(btnStartX + slot * step, rowCY, 0.001),
                () => onBandRoleSelect(i, role), drawBandRoleButton);
            b.userData_bandRow = row;
            b.userData_role = role;
            return b;
        };
        row.L     = mkBtn('L',     'left',  0);
        row.R     = mkBtn('R',     'right', 1);
        row.clear = mkBtn('CLEAR', '',      2);
        configBandRows.push(row);
        by -= CONFIG_ROW_H + CONFIG_ROW_GAP;
    }

    // Capture-row slots. We create CONFIG_LIST_ROWS fixed-position row buttons
    // up front; setCaptures() binds each to a capture (or hides extras).
    let y = by;
    for (let i = 0; i < CONFIG_LIST_ROWS; i++) {
        const rowY = y - CONFIG_ROW_H / 2;
        const btn = createConfigButton(
            `CAPROW_${i}`, CONFIG_ROW_W, CONFIG_ROW_H,
            new THREE.Vector3(0, rowY, 0.001),
            () => onCaptureRowSelect(i), drawCaptureRow);
        btn.userData_rowIndex = i;
        btn.userData_capture = null;   // bound in setCaptures
        configRowButtons.push(btn);
        y -= CONFIG_ROW_H + CONFIG_ROW_GAP;
    }

    // REPLAY / STOP REPLAY action button.
    const actY = y - CONFIG_ROW_H / 2;
    createConfigButton('REPLAY_ACTION', CONFIG_ROW_W, CONFIG_ROW_H,
                       new THREE.Vector3(0, actY, 0.001),
                       onReplayAction, drawReplayAction);
    y -= CONFIG_ROW_H + CONFIG_ROW_GAP;

    // Speed toggle.
    const spdY = y - CONFIG_ROW_H / 2;
    createConfigButton('REPLAY_SPEED', CONFIG_ROW_W, CONFIG_ROW_H,
                       new THREE.Vector3(0, spdY, 0.001),
                       onSpeedToggle, drawSpeedToggle);
    y -= CONFIG_ROW_H + CONFIG_ROW_GAP;

    // --- TRAIN + MODELS section (below REPLAY) ------------------------------

    // Static "TRAIN -- per-hand models" section header (plain canvas label).
    const trainHdrCanvas = document.createElement('canvas');
    trainHdrCanvas.width = 512; trainHdrCanvas.height = 80;
    const thc = trainHdrCanvas.getContext('2d');
    thc.fillStyle = '#9fb3c8';
    thc.font = 'bold 30px system-ui';
    thc.textBaseline = 'middle';
    thc.textAlign = 'left';
    thc.fillText('TRAIN · per-hand models', 8, trainHdrCanvas.height / 2);
    const trainHdrTex = new THREE.CanvasTexture(trainHdrCanvas);
    trainHdrTex.colorSpace = THREE.SRGBColorSpace;
    const trainHdrMesh = new THREE.Mesh(
        new THREE.PlaneGeometry(CONFIG_ROW_W, CONFIG_ROW_H),
        new THREE.MeshBasicMaterial({ map: trainHdrTex, transparent: true,
                                       depthWrite: false }));
    trainHdrMesh.position.set(0, y - CONFIG_ROW_H / 2, 0.001);
    configPanel.add(trainHdrMesh);
    y -= CONFIG_ROW_H + CONFIG_ROW_GAP;

    // TRAIN button row: TRAIN BOTH (wide) + TRAIN L + TRAIN R, left-to-right.
    // All three share one row's vertical center. x=0 is the row center.
    const trainRowCY = y - CONFIG_ROW_H / 2;
    const trLeft = -CONFIG_ROW_W / 2;
    const bothCX = trLeft + CONFIG_TRAIN_BOTH_W / 2;
    const lCX = trLeft + CONFIG_TRAIN_BOTH_W + CONFIG_TRAIN_BTN_GAP
        + CONFIG_TRAIN_ONE_W / 2;
    const rCX = lCX + CONFIG_TRAIN_ONE_W + CONFIG_TRAIN_BTN_GAP;
    const both = createConfigButton('TRAIN_BOTH', CONFIG_TRAIN_BOTH_W, CONFIG_ROW_H,
                       new THREE.Vector3(bothCX, trainRowCY, 0.001),
                       () => trainBoth(), drawTrainButton);
    both.userData_trainLabel = 'TRAIN BOTH';
    const trL = createConfigButton('TRAIN_L', CONFIG_TRAIN_ONE_W, CONFIG_ROW_H,
                       new THREE.Vector3(lCX, trainRowCY, 0.001),
                       () => trainBoth('left'), drawTrainButton);
    trL.userData_trainLabel = 'TR L';
    const trR = createConfigButton('TRAIN_R', CONFIG_TRAIN_ONE_W, CONFIG_ROW_H,
                       new THREE.Vector3(rCX, trainRowCY, 0.001),
                       () => trainBoth('right'), drawTrainButton);
    trR.userData_trainLabel = 'TR R';
    y -= CONFIG_ROW_H + CONFIG_ROW_GAP;

    // MODEL L / MODEL R rows: a wide info label (current model name) + a PICK
    // sub-button that cycles the registry + loads it for that side.
    for (const role of ['left', 'right']) {
        const rowCY = y - CONFIG_ROW_H / 2;
        const infoCanvas = document.createElement('canvas');
        infoCanvas.width = 512; infoCanvas.height = 80;
        const infoCtx = infoCanvas.getContext('2d');
        const infoTex = new THREE.CanvasTexture(infoCanvas);
        infoTex.colorSpace = THREE.SRGBColorSpace;
        const infoMesh = new THREE.Mesh(
            new THREE.PlaneGeometry(CONFIG_MODEL_INFO_W, CONFIG_ROW_H),
            new THREE.MeshBasicMaterial({ map: infoTex, transparent: true,
                                           depthWrite: false }));
        infoMesh.position.set(trLeft + CONFIG_MODEL_INFO_W / 2, rowCY, 0.001);
        configPanel.add(infoMesh);
        const row = {
            role,
            info: { mesh: infoMesh, canvas: infoCanvas, ctx: infoCtx, tex: infoTex },
            _lastInfoKey: null,
        };
        const pickCX = trLeft + CONFIG_MODEL_INFO_W + CONFIG_BAND_BTN_GAP
            + CONFIG_MODEL_BTN_W / 2;
        row.pick = createConfigButton(
            `MODEL_${role === 'left' ? 'L' : 'R'}_PICK`,
            CONFIG_MODEL_BTN_W, CONFIG_ROW_H,
            new THREE.Vector3(pickCX, rowCY, 0.001),
            () => onModelPick(role), drawModelPickButton);
        row.pick.userData_modelRole = role;
        configModelRows.push(row);
        y -= CONFIG_ROW_H + CONFIG_ROW_GAP;
    }

    // Drag handle in the top-left corner, like the other panels.
    createDragHandle(configPanel,
                     new THREE.Vector3(-CONFIG_W / 2 - HANDLE_SIZE_M,
                                        CONFIG_H / 2 + HANDLE_SIZE_M, 0));

    configPanel.visible = false;
    menuRoot.add(configPanel);
    // Sits to the right of the menu cluster (local offset within menuRoot).
    configPanel.position.set(CONFIG_OFFSET_RIGHT, 0, 0.002);

    // First paint so it looks right the instant it's shown.
    setCaptures(configState.captures);
    drawBandsSection(latestSnapshot);   // hides all band slots until devices appear
    setModels(configState.models);      // TRAIN buttons + model rows (empty until fetch)
    drawConfigTitle();
}

// Bind the cached capture list onto the fixed row slots. Extra rows (beyond
// the capture count) are hidden; an empty list shows a single "no captures
// yet" placeholder. Re-renders each affected row.
function setCaptures(list) {
    configState.captures = Array.isArray(list) ? list : [];
    const caps = configState.captures;
    for (let i = 0; i < configRowButtons.length; i++) {
        const btn = configRowButtons[i];
        if (caps.length === 0) {
            // Show the placeholder on row 0 only; hide the rest.
            btn.userData_capture = null;
            btn.mesh.visible = (i === 0);
        } else if (i < caps.length) {
            btn.userData_capture = caps[i];
            btn.mesh.visible = true;
        } else {
            btn.userData_capture = null;
            btn.mesh.visible = false;
        }
        btn._lastDrawKey = null;            // force a repaint
        drawCaptureRow(btn, false);
    }
    return caps;
}

// Row-select handler: choose the capture bound to row `i` (no-op for the
// empty-list placeholder). Selection drives the highlight + the REPLAY POST.
function onCaptureRowSelect(i) {
    const btn = configRowButtons[i];
    const cap = btn && btn.userData_capture;
    if (!cap) return;
    selectCapture(cap.name);
}

// Mark `name` as the chosen capture and force the rows to repaint so the
// highlight moves. Exposed on window.OMVR for headless driving.
function selectCapture(name) {
    configState.selected = name;
    for (const btn of configRowButtons) { btn._lastDrawKey = null; }
    setStatus(`replay capture: ${name}`);
}

// Fetch the capture list (GET /api/captures) and render it. Called when the
// SETUP panel opens (re-fetch on every open so the list is current). Each item
// from list_captures has {name, size_bytes, mtime, meta}; we also derive a row
// count for display. list_captures doesn't include a row count, so we show the
// file size as a proxy when rows are unknown (see note in report).
async function fetchCaptures() {
    configState.loading = true;
    drawConfigTitle();
    try {
        const r = await fetch('/api/captures');
        if (!r.ok) {
            setStatus(`captures load failed: HTTP ${r.status}`);
            setCaptures([]);
            return;
        }
        const list = await r.json();
        // Normalize: surface a `rows` field for the row renderer. list_captures
        // returns size_bytes (not a row count); we expose KB so the operator
        // still gets a size cue. If a future backend adds rows, it wins.
        const norm = (Array.isArray(list) ? list : []).map((c) => ({
            ...c,
            rows: (typeof c.rows === 'number')
                ? c.rows
                : Math.max(1, Math.round((c.size_bytes || 0) / 1024)) + 'KB',
        }));
        setCaptures(norm);
    } catch (e) {
        setStatus(`captures load error: ${e.message}`);
        setCaptures([]);
    } finally {
        configState.loading = false;
        drawConfigTitle();
    }
}

// REPLAY action: start or stop a replay depending on the live snapshot state.
// Start -> POST /api/simulate/replay {capture, speed}; a 409 (recording or
// another replay active) surfaces the server's detail in the status strip.
// Stop -> DELETE /api/simulate/replay.
async function onReplayAction() {
    const rp = (latestSnapshot && latestSnapshot.replay) || { active: false };
    try {
        if (rp.active) {
            const r = await fetch('/api/simulate/replay', { method: 'DELETE' });
            if (r.ok) setStatus('replay stopped');
            else      setStatus(`stop replay failed: HTTP ${r.status}`);
            return;
        }
        if (!configState.selected) {
            setStatus('pick a capture to replay first');
            return;
        }
        const r = await fetch('/api/simulate/replay', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ capture: configState.selected,
                                   speed: configState.speed }),
        });
        if (r.ok) {
            const data = await r.json();
            setStatus(`replaying ${data.capture} (${data.rows} rows, ${data.speed}x)`);
        } else {
            // 409 = recording or another replay active. Surface the server's
            // detail ("stop the recording first" etc.) verbatim.
            let detail = `HTTP ${r.status}`;
            try {
                const err = await r.json();
                if (err && err.detail) detail = err.detail;
            } catch (e) { /* non-JSON error body */ }
            setStatus(`replay failed: ${detail}`.slice(0, 80));
        }
    } catch (e) {
        setStatus(`replay error: ${e.message}`);
    }
}

// Speed toggle: cycle 1x -> 2x -> 1x. Sent as the `speed` field on the next
// REPLAY POST. (Changing speed mid-replay needs a stop + restart; the toggle
// only affects the NEXT start, matching the locked design.)
function onSpeedToggle() {
    configState.speed = configState.speed >= 2.0 ? 1.0 : 2.0;
    setStatus(`replay speed ${configState.speed}x`);
}

// Render the whole REPLAY section given a snapshot (used live each frame and
// for headless preview via window.OMVR.drawReplaySection(fakeSnap)). Title +
// action button track the snapshot's replay state; rows track selection. The
// per-button customDraw hooks read latestSnapshot, so for a headless fake we
// temporarily point latestSnapshot at the supplied snap.
function drawReplaySection(snap) {
    const prev = latestSnapshot;
    if (snap !== undefined) latestSnapshot = snap;
    try {
        drawConfigTitle();
        for (const btn of configRowButtons) {
            if (btn.mesh.visible) drawCaptureRow(btn, btn === hoveredButton);
        }
        const action = buttons['REPLAY_ACTION'];
        if (action) drawReplayAction(action, action === hoveredButton);
        const speed = buttons['REPLAY_SPEED'];
        if (speed) drawSpeedToggle(speed, speed === hoveredButton);
    } finally {
        if (snap !== undefined) latestSnapshot = prev;
    }
}

// ---------------------------------------------------------------------------
// TRAIN + MODELS section (per-hand training + per-hand model loading)
//
// The final headset-only controls: train model_left + model_right from the SAME
// capture(s) and activate either side, all without taking the headset off. Three
// TRAIN buttons (TRAIN BOTH / TRAIN L / TRAIN R) plus two MODEL rows (MODEL L /
// MODEL R) that cycle through the registry (GET /api/models) and load the chosen
// model into the per-hand slot (POST /api/inference/model/{role}).
//
// TRAIN BOTH reuses pickTrainCaptures() (active session, else most recent) and
// POSTs /api/train TWICE -- role:"left" then role:"right" -- on the SAME
// captures, so the two single-arm models come from one recording. On success it
// flips inference on (POST /api/inference/enabled) like the menu TRAIN button.
// All buttons live in buttons[] with a customDraw hook so the existing
// raycast/pinch/cooldown path drives them; no new input system.
// ---------------------------------------------------------------------------

// TRAIN button paint (TRAIN BOTH / TRAIN L / TRAIN R). Shows a spinner while a
// train sequence is in flight; muted while busy so a second tap reads as inert.
function drawTrainButton(btn, hovered) {
    const flashing = (btn.flashUntil || 0) > performance.now();
    const busy = configState.trainBusy;
    // Animated spinner glyph cycles while busy (time-derived, so the redraw-skip
    // key changes each tick and the spinner actually turns).
    const spin = busy
        ? ['|', '/', '-', '\\'][Math.floor(performance.now() / 120) % 4]
        : '';
    const label = busy ? `${btn.userData_trainLabel} ${spin}` : btn.userData_trainLabel;
    const drawKey = `${hovered}|${flashing}|${busy}|${label}`;
    if (btn._lastDrawKey === drawKey) return;
    btn._lastDrawKey = drawKey;
    const ctx = btn.ctx;
    const W = btn.canvas.width, H = btn.canvas.height;
    ctx.clearRect(0, 0, W, H);
    let bg;
    if (flashing)     bg = '#ffffff';
    else if (busy)    bg = '#374151';   // muted while a train is running
    else if (hovered) bg = '#1f6feb';
    else              bg = '#15803d';   // emerald: train is a "go" action
    ctx.fillStyle = bg;
    _roundRectPath(ctx, W, H, 14);
    ctx.fill();
    ctx.fillStyle = flashing ? '#0d1117' : '#f0f4f8';
    ctx.font = 'bold 30px system-ui';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, W / 2, H / 2);
    btn.tex.needsUpdate = true;
}

// Model-row info label paint: which model is loaded for this side. Pulls the
// per-side name from configState (set by the train/load responses) and falls
// back to the combined snapshot inference.model string so a model loaded from
// the PC still shows. Not a button -- raycast never targets it.
function drawModelInfoLabel(row) {
    const side = row.role;                                  // 'left' | 'right'
    const loaded = side === 'left' ? configState.modelLeft : configState.modelRight;
    // Combined fallback: snapshot inference.model is a single string covering
    // whatever the engine has loaded (may be pooled). Shown dimmer as a hint.
    const snapModel = (latestSnapshot && latestSnapshot.inference
        && latestSnapshot.inference.model) || '';
    const name = loaded || '';
    const drawKey = `${side}|${name}|${snapModel}`;
    if (row._lastInfoKey === drawKey) return;
    row._lastInfoKey = drawKey;
    const ctx = row.info.ctx;
    const W = row.info.canvas.width, H = row.info.canvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#2a3142';
    _roundRectPath(ctx, W, H, 16);
    ctx.fill();
    // Side badge (L red / R blue), then the loaded model name (or a hint).
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    ctx.font = 'bold 30px system-ui';
    ctx.fillStyle = side === 'left' ? '#ef4444' : '#1f6feb';
    const badge = side === 'left' ? 'L' : 'R';
    ctx.fillText(badge, 16, H / 2);
    ctx.fillStyle = '#f0f4f8';
    ctx.font = '28px system-ui';
    let txt;
    if (name) {
        txt = name.length > 26 ? name.slice(0, 25) + '…' : name;
    } else if (snapModel) {
        txt = `(engine: ${snapModel.length > 18 ? snapModel.slice(0, 17) + '…' : snapModel})`;
    } else {
        txt = 'no model -- PICK to load';
    }
    ctx.fillText(txt, 54, H / 2);
    row.info.tex.needsUpdate = true;
}

// PICK sub-button paint (one per model row): cycles to + loads the next model.
function drawModelPickButton(btn, hovered) {
    const flashing = (btn.flashUntil || 0) > performance.now();
    const enabled = (configState.models || []).length > 0;
    const drawKey = `${hovered}|${flashing}|${enabled}`;
    if (btn._lastDrawKey === drawKey) return;
    btn._lastDrawKey = drawKey;
    const ctx = btn.ctx;
    const W = btn.canvas.width, H = btn.canvas.height;
    ctx.clearRect(0, 0, W, H);
    let bg;
    if (flashing)      bg = '#ffffff';
    else if (!enabled) bg = '#374151';   // muted: no models trained yet
    else if (hovered)  bg = '#1f6feb';
    else               bg = '#2563eb';
    ctx.fillStyle = bg;
    _roundRectPath(ctx, W, H, 12);
    ctx.fill();
    ctx.fillStyle = flashing ? '#0d1117' : '#f0f4f8';
    ctx.font = 'bold 26px system-ui';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('PICK', W / 2, H / 2);
    btn.tex.needsUpdate = true;
}

// Train one role: POST /api/train {captures, activate:true, role}. Returns the
// parsed result on success, or throws with the server's detail so the caller
// can surface it. role in {'left','right'}.
async function trainOneRole(captures, role) {
    const r = await fetch('/api/train', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ captures, activate: true, role }),
    });
    if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const err = await r.json(); if (err && err.detail) detail = err.detail; }
        catch (e) { /* non-JSON error body */ }
        throw new Error(detail);
    }
    return r.json();
}

// TRAIN BOTH: train model_left + model_right on the SAME capture(s) in sequence.
// Reuses pickTrainCaptures() so the picks match the menu TRAIN button. Shows a
// spinner (configState.trainBusy drives drawTrainButton), then a per-side R²
// summary in the row + status strip. On a successful pair it flips inference on
// so predictions start (matching the menu TRAIN button's auto-enable). Surfaces
// the server's detail on error. roleOverride trains a SINGLE side (TRAIN L /
// TRAIN R reuse this path with one role).
async function trainBoth(roleOverride) {
    if (configState.trainBusy || uiState.training) {
        setStatus('training already in flight -- wait for it to finish');
        return;
    }
    if (uiState.recording) {
        setStatus('stop recording before training');
        return;
    }
    const roles = roleOverride ? [roleOverride] : ['left', 'right'];
    configState.trainBusy = true;
    uiState.training = true;     // also gates the menu TRAIN button + PREDICT precheck
    configState.trainResult = '';
    try {
        const captures = await pickTrainCaptures();
        if (captures.length === 0) {
            setStatus('no captures to train on -- record something first');
            return;
        }
        setStatus(`training ${roles.join(' + ')} on ${captures.length} capture(s)…`);
        const parts = [];
        let anyActive = false;
        for (const role of roles) {
            const result = await trainOneRole(captures, role);
            const r2 = result.metrics?.r2;
            const r2str = (typeof r2 === 'number') ? r2.toFixed(2) : '?';
            const tag = role === 'left' ? 'L' : 'R';
            parts.push(`${tag} R²=${r2str}`);
            if (result.active) {
                anyActive = true;
                // The single-arm model is now loaded into this role's slot;
                // mirror its name into the per-side display.
                const nm = modelNameFromPath(result.model_path) || `${role} model`;
                if (role === 'left')  configState.modelLeft = nm;
                else                  configState.modelRight = nm;
            }
        }
        configState.trainResult = parts.join(' / ');
        // Auto-enable prediction after a successful train, like the menu TRAIN
        // button (in VR there's no obvious second click to start inference).
        let predictTail = '';
        if (anyActive) {
            try {
                const er = await fetch('/api/inference/enabled', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: true }),
                });
                if (er.ok) predictTail = ' · predict ON';
            } catch (e) { /* non-fatal */ }
        }
        setStatus(`trained ${configState.trainResult}` +
                  (anyActive ? ' · loaded ✓' : ' (saved only)') + predictTail);
        // Refresh the model registry so the new models show in the MODEL rows.
        fetchModels();
    } catch (e) {
        configState.trainResult = `train failed: ${e.message}`.slice(0, 60);
        setStatus(`train failed: ${e.message}`.slice(0, 80));
    } finally {
        configState.trainBusy = false;
        uiState.training = false;
    }
}

// Derive a short model display name from a model.pkl path. The registry stores
// each model under data/models/<name>_<timestamp>/model.pkl, so the parent dir
// name is the most informative label. Robust to forward/back slashes.
function modelNameFromPath(path) {
    if (!path || typeof path !== 'string') return '';
    const parts = path.split(/[\\/]/).filter(Boolean);
    // parts: [..., '<name>_<ts>', 'model.pkl'] -- the dir is the second-to-last.
    if (parts.length >= 2) return parts[parts.length - 2];
    return parts[parts.length - 1] || '';
}

// Cache the model registry list + repaint the MODEL rows. Exposed on window.OMVR
// for headless driving (inject a fake list without a /api/models round-trip).
function setModels(list) {
    configState.models = Array.isArray(list) ? list : [];
    // Keep each side's cycle cursor pointed at its currently-loaded model (by
    // name) so the NEXT PICK advances from there rather than restarting at 0.
    // A PC-loaded model we can't attribute to a side is left to the snapshot
    // fallback in drawModelInfoLabel.
    for (const role of ['left', 'right']) {
        const cur = role === 'left' ? configState.modelLeft : configState.modelRight;
        if (cur) {
            const idx = configState.models.findIndex(
                (m) => m && modelNameFromPath(m.path) === cur);
            if (idx >= 0) configState.modelIdx[role] = idx;
        }
    }
    drawTrainSection();
    return configState.models;
}

// Fetch the model registry (GET /api/models) and cache it. Called when the
// SETUP panel opens + after a successful train. Each item from list_models has
// {name, path, created, metrics, active}.
async function fetchModels() {
    try {
        const r = await fetch('/api/models');
        if (!r.ok) {
            setStatus(`models load failed: HTTP ${r.status}`);
            setModels([]);
            return;
        }
        const list = await r.json();
        setModels(Array.isArray(list) ? list : []);
    } catch (e) {
        setStatus(`models load error: ${e.message}`);
        setModels([]);
    }
}

// Load a specific model into a per-hand slot: POST /api/inference/model/{role}
// {path}. Mirrors the returned model name into the per-side display + flips
// inference on so predictions start. Surfaces the server's detail on error.
// Exposed on window.OMVR for headless driving.
async function loadModelForRole(role, path) {
    if (role !== 'left' && role !== 'right') {
        setStatus(`bad role: ${role}`);
        return;
    }
    try {
        const r = await fetch(`/api/inference/model/${role}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        });
        if (!r.ok) {
            let detail = `HTTP ${r.status}`;
            try { const err = await r.json(); if (err && err.detail) detail = err.detail; }
            catch (e) { /* non-JSON error body */ }
            setStatus(`load ${role} failed: ${detail}`.slice(0, 80));
            return;
        }
        const data = await r.json();
        const nm = data.model || modelNameFromPath(path) || `${role} model`;
        if (role === 'left')  configState.modelLeft = nm;
        else                  configState.modelRight = nm;
        // Auto-enable prediction after a load, like TRAIN -- there's no obvious
        // second click in VR to start inference.
        let predictTail = '';
        try {
            const er = await fetch('/api/inference/enabled', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: true }),
            });
            if (er.ok) predictTail = ' · predict ON';
        } catch (e) { /* non-fatal */ }
        setStatus(`${role} model: ${nm}${predictTail}`);
        drawTrainSection();
    } catch (e) {
        setStatus(`load ${role} error: ${e.message}`);
    }
}

// PICK handler: advance this side's cursor to the next model in the registry
// and load it. With no models trained yet it's a no-op (the button is muted).
function onModelPick(role) {
    const models = configState.models || [];
    if (models.length === 0) {
        setStatus('no models yet -- TRAIN one first');
        return;
    }
    const next = (configState.modelIdx[role] + 1) % models.length;
    configState.modelIdx[role] = next;
    const m = models[next];
    if (!m || !m.path) {
        setStatus('model has no path');
        return;
    }
    loadModelForRole(role, m.path);
}

// Render the whole TRAIN + MODELS section. Repaints the three TRAIN buttons,
// the two model-row info labels, and the PICK sub-buttons. Used live each frame
// (while the panel is open) and headless via window.OMVR.drawTrainSection(snap).
// Like drawReplaySection, a supplied snap is temporarily pointed at by
// latestSnapshot so the info-label fallback reads it.
function drawTrainSection(snap) {
    const prev = latestSnapshot;
    if (snap !== undefined) latestSnapshot = snap;
    try {
        for (const name of ['TRAIN_BOTH', 'TRAIN_L', 'TRAIN_R']) {
            const btn = buttons[name];
            if (btn) drawTrainButton(btn, btn === hoveredButton);
        }
        for (const row of configModelRows) {
            drawModelInfoLabel(row);
            drawModelPickButton(row.pick, row.pick === hoveredButton);
        }
    } finally {
        if (snap !== undefined) latestSnapshot = prev;
    }
}

// SETUP button handler: toggle the config panel. Opening re-fetches the
// capture list so it's current.
function toggleSetup() {
    if (configState.open) closeSetup();
    else openSetup();
}

function openSetup() {
    configState.open = true;
    if (configPanel) configPanel.visible = true;
    fetchCaptures();          // re-fetch the capture list on every open
    fetchModels();            // re-fetch the model registry on every open
}

function closeSetup() {
    configState.open = false;
    if (configPanel) configPanel.visible = false;
}

// ---------------------------------------------------------------------------
// Drag handles + collapsed STOP button (v1.10)
// ---------------------------------------------------------------------------

function createDragHandle(target, localOffset) {
    const geo = new THREE.BoxGeometry(HANDLE_SIZE_M, HANDLE_SIZE_M, HANDLE_SIZE_M * 0.4);
    const mat = new THREE.MeshStandardMaterial({
        color: HANDLE_COLOR_IDLE,
        emissive: HANDLE_COLOR_IDLE,
        emissiveIntensity: 0.35,
        metalness: 0.3,
        roughness: 0.6,
    });
    const handle = new THREE.Mesh(geo, mat);
    handle.position.copy(localOffset);
    handle.userData.dragTarget = target;   // raycast looks this up
    target.add(handle);
    dragHandles.push({ handle, target, mat });
    return handle;
}

// Tracks the last hover state we actually rendered so we can skip the
// redraw (and the GPU texture re-upload) when nothing changed. The STOP
// button is visible for the whole recording but its appearance only flips
// on hover enter/leave, so without this guard we'd re-upload a 512x280
// texture every frame at 72fps -- wasted bandwidth competing with hand
// tracking exactly when smooth tracking matters most.
let _stopButtonLastHovered = null;

function drawStopButton(hovered) {
    if (_stopButtonLastHovered === hovered) return;   // no change, skip
    _stopButtonLastHovered = hovered;
    const ctx = stopButtonCtx;
    const W = stopButtonCanvas.width, H = stopButtonCanvas.height;
    ctx.clearRect(0, 0, W, H);

    // Big red button, white STOP text. Hover state brightens.
    ctx.fillStyle = hovered ? '#ff6b6b' : '#ef4444';
    const r = 30;
    ctx.beginPath();
    ctx.moveTo(r, 0);
    ctx.lineTo(W - r, 0); ctx.arcTo(W, 0, W, r, r);
    ctx.lineTo(W, H - r); ctx.arcTo(W, H, W - r, H, r);
    ctx.lineTo(r, H);     ctx.arcTo(0, H, 0, H - r, r);
    ctx.lineTo(0, r);     ctx.arcTo(0, 0, r, 0, r);
    ctx.closePath();
    ctx.fill();

    // White inset border for definition against bright AR backgrounds
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 6;
    ctx.stroke();

    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 120px system-ui';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('STOP', W / 2, H / 2);

    stopButtonTex.needsUpdate = true;
}

// Swap menu visibility based on recording state. Called each frame from
// updateFromSnapshot so it tracks whatever the server actually reports
// (catches cases where another client started/stopped recording too).
function setRecordingCollapsedUI(isRecording) {
    menuPanel.visible = !isRecording;
    stopButtonMesh.visible = !!isRecording;
    // Status strip stays visible in both states -- it surfaces save/train
    // feedback that's useful regardless of the mode.
}

function startDrag(target, controller, handleMat) {
    dragState.target = target;
    dragState.controller = controller;
    dragState.grabOffset.copy(target.position).sub(controller.position);
    dragState.handleMat = handleMat;
    dragState.endAt = 0;
    if (handleMat) {
        handleMat.color.setHex(HANDLE_COLOR_GRAB);
        handleMat.emissive.setHex(HANDLE_COLOR_GRAB);
        handleMat.emissiveIntensity = 0.5;
    }
}

function endDrag() {
    if (!dragState.target) return;
    // Re-aim the panel at the head's current position so it ends up facing
    // the user no matter where they let go. Without this, dragging behind
    // your back would leave the panel facing the wrong way.
    const head = renderer.xr.getCamera ? renderer.xr.getCamera() : camera;
    dragState.target.lookAt(head.position);
    // menuRoot has a tilt baked in via placeAnchors; preserve it on release.
    if (dragState.target === menuRoot) {
        dragState.target.rotateX(THREE.MathUtils.degToRad(MENU_TILT_DEG));
    }
    if (dragState.handleMat) {
        dragState.handleMat.color.setHex(HANDLE_COLOR_IDLE);
        dragState.handleMat.emissive.setHex(HANDLE_COLOR_IDLE);
        dragState.handleMat.emissiveIntensity = 0.35;
    }
    dragState.target = null;
    dragState.controller = null;
    dragState.handleMat = null;
    dragState.endAt = 0;
    // Persist whatever the user just settled on so it survives session
    // reloads (and pause/resume). Per-MODE key because VR and AR should
    // have independent layouts -- you might dock the menu top-right in
    // VR for gesture training but want it at-waist for AR field capture.
    savePanelLayout();
}

// Abnormal drag termination: the controller disconnected (hand tracking
// lost mid-drag) or the session paused (boundary redraw) while a panel was
// being held. In these cases `selectend` never fires, so without this the
// drag would be orphaned -- dragState.target stays set, the handle stays
// stuck GRAB-green, and updateDrag() keeps gluing the panel to a frozen or
// stale controller position. cancelDrag() releases the panel wherever it
// currently is (no head re-orientation, since head pose is unreliable
// during a pause), restores the handle color, and persists the layout.
function cancelDrag() {
    if (!dragState.target) return;
    if (dragState.handleMat) {
        dragState.handleMat.color.setHex(HANDLE_COLOR_IDLE);
        dragState.handleMat.emissive.setHex(HANDLE_COLOR_IDLE);
        dragState.handleMat.emissiveIntensity = 0.35;
    }
    dragState.target = null;
    dragState.controller = null;
    dragState.handleMat = null;
    dragState.endAt = 0;
    savePanelLayout();
}

// ---------------------------------------------------------------------------
// Panel layout persistence (localStorage, per-MODE)
//
// Save the user's preferred infoGroup / menuRoot poses so they don't have
// to re-drag the cluster every time they reload the page or come back from
// a pause. Stored as world-space transforms in the local-floor reference
// frame -- works for the common case where the user spawns in roughly the
// same spot at their PC each session. If they spawn somewhere very
// different the saved layout may be in the wrong place, in which case
// RECENTER resets to defaults.
// ---------------------------------------------------------------------------

const LAYOUT_STORAGE_KEY = `openmuscle-vr-layout-${MODE}`;

function _poseToJSON(obj3d) {
    return {
        p: [obj3d.position.x, obj3d.position.y, obj3d.position.z],
        q: [obj3d.quaternion.x, obj3d.quaternion.y, obj3d.quaternion.z, obj3d.quaternion.w],
        s: obj3d.scale.x,        // uniform scale (we only ever setScalar)
    };
}

// Validate that every element of `arr` is a finite number and the array is
// exactly `len` long. Guards against corrupt / partially-written / schema-
// drifted localStorage payloads that would otherwise set NaN positions and
// fling a panel to an unreachable spot the user can't even find to drag back.
function _allFinite(arr, len) {
    if (!Array.isArray(arr) || arr.length !== len) return false;
    for (const v of arr) {
        if (typeof v !== 'number' || !Number.isFinite(v)) return false;
    }
    return true;
}

function _applyPoseJSON(obj3d, pose) {
    if (!pose || !_allFinite(pose.p, 3) || !_allFinite(pose.q, 4)) return false;
    // Reject a degenerate (zero-length) quaternion -- it can't be normalized
    // and would leave the object's orientation undefined.
    const [qx, qy, qz, qw] = pose.q;
    if (qx * qx + qy * qy + qz * qz + qw * qw < 1e-6) return false;
    const scale = (typeof pose.s === 'number' && Number.isFinite(pose.s) && pose.s > 0)
        ? pose.s : 1.0;
    obj3d.position.set(pose.p[0], pose.p[1], pose.p[2]);
    obj3d.quaternion.set(qx, qy, qz, qw).normalize();
    obj3d.scale.setScalar(scale);
    return true;
}

function savePanelLayout() {
    try {
        const payload = {
            v: 1,                            // schema version, bump if structure changes
            info: _poseToJSON(infoGroup),
            menu: _poseToJSON(menuRoot),
        };
        localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(payload));
    } catch (e) {
        // localStorage quota errors / private mode / etc. Not fatal.
        console.warn('[openmuscle-vr] savePanelLayout failed:', e);
    }
}

function loadPanelLayout() {
    try {
        const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
        if (!raw) return null;
        const payload = JSON.parse(raw);
        if (!payload || payload.v !== 1) return null;
        return payload;
    } catch (e) {
        console.warn('[openmuscle-vr] loadPanelLayout failed:', e);
        return null;
    }
}

function clearPanelLayout() {
    try { localStorage.removeItem(LAYOUT_STORAGE_KEY); }
    catch (e) { /* swallow */ }
}

function updateDrag() {
    if (!dragState.target || !dragState.controller) return;
    // A queued pinch-release that wasn't cancelled by a re-pinch within the
    // grace window: actually end the drag now.
    if (dragState.endAt && performance.now() - dragState.endAt >= DRAG_END_GRACE_MS) {
        endDrag();
        return;
    }
    dragState.target.position
        .copy(dragState.controller.position)
        .add(dragState.grabOffset);
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
    // RECENTER = reset to default positions + forget any persisted layout.
    // Without the clear, a user who got confused, hit RECENTER, then closed
    // the tab would come back next session to the same lost layout. The
    // "I want a clean slate" gesture needs to actually clean the slate.
    clearPanelLayout();
    placed = false;
    setStatus('UI re-centered (defaults restored, saved layout cleared)');
}

// Pinch on a controller -> activate whichever button the ray is currently over,
// IF that controller is the off-hand. The captured-arm pinch is handled by
// detectPinchAndToggle (1-second hold required for record toggle) so we
// don't double-fire here.
function onControllerSelect(ctrl) {
    const handedness = ctrl.userData?.handedness;
    if (handedness === ARM) return;       // captured arm: handled elsewhere

    // Mid-drag re-pinch: a brief pinch dropout queued a drag-end (endAt). The
    // pinch came back within the grace window, so cancel the end + keep moving
    // the panel rather than starting a new grab / firing a button.
    if (dragState.controller === ctrl && dragState.endAt) {
        dragState.endAt = 0;
        return;
    }

    // Priority 1: hovered drag handle -> start a drag (preempts buttons)
    if (hoveredHandle) {
        startDrag(hoveredHandle.target, ctrl, hoveredHandle.mat);
        return;
    }

    // Priority 2: hovered collapsed STOP button (visible only while recording)
    if (stopButtonMesh.visible && hoveredButton === null
            && _hoveredMeshIsStopButton) {
        // Treat as a STOP press = trigger toggleRecording (which will stop).
        const now = performance.now();
        if (now - _stopLastActivateAt > BUTTON_COOLDOWN_MS) {
            _stopLastActivateAt = now;
            try { toggleRecording(); }
            catch (e) { console.error('STOP failed:', e); }
        }
        return;
    }

    // Priority 3: hovered regular menu button -> activate
    if (!hoveredButton) return;
    const now = performance.now();
    if (now - hoveredButton.lastActivateAt < BUTTON_COOLDOWN_MS) return;
    hoveredButton.lastActivateAt = now;
    hoveredButton.flashUntil = now + BUTTON_FLASH_MS;
    try { hoveredButton.onActivate(); }
    catch (e) { console.error(`button ${hoveredButton.name} failed:`, e); }
}

// Track collapsed STOP hover state separately from the menu-button hover
// state since stopButtonMesh isn't in the `buttons` map. The raycast logic
// sets these each frame.
let _hoveredMeshIsStopButton = false;
let _stopLastActivateAt = 0;

// Per-frame: raycast each controller against the menu buttons, highlight the
// hovered one, shorten the visible ray to the hit point. Only the off-hand's
// ray is visible -- the captured arm's ray would clutter the view and risk
// hovering buttons while you're trying to perform a gesture.
const _tmpMat = new THREE.Matrix4();
function updateRaycast() {
    // Drag handles + the STOP button reset each frame (immediate -- those are
    // deliberate grabs). hoveredButton is NOT reset here: it is debounced via
    // _settleHover below so hand-tracking jitter can't flicker the highlight.
    hoveredHandle = null;
    _hoveredMeshIsStopButton = false;
    let rawHitButton = null;          // the button the ray points at THIS frame

    // Build the raycast target list. Drag handles take precedence over
    // buttons when both are reachable -- but Three.js's raycaster returns
    // hits sorted by distance, so we mix both sets and let geometry decide.
    // Visible-only filter: hidden meshes shouldn't be hoverable.
    // Menu buttons are hoverable only while the menu grid is shown; config-
    // panel buttons (capture rows, REPLAY, speed) only while the SETUP panel
    // is open. Both still funnel through the same buttons[] + raycast path.
    const configOpen = !!(configPanel && configPanel.visible);
    const buttonMeshes = Object.values(buttons)
        .filter(b => b.mesh.visible
            && (b.isConfig ? configOpen : menuPanel.visible))
        .map(b => b.mesh);
    const handleMeshes = dragHandles
        .filter(h => h.target.visible)
        .map(h => h.handle);
    const stopMeshList = stopButtonMesh.visible ? [stopButtonMesh] : [];
    const allTargets = [...buttonMeshes, ...handleMeshes, ...stopMeshList];

    for (let i = 0; i < controllers.length; i++) {
        const ctrl = controllers[i];
        const line = controllerRays[i];
        const handedness = ctrl.userData?.handedness;

        // Hide the captured-arm's ray + skip its raycast. Also: while THIS
        // controller is dragging, keep its ray visible + amber so the user
        // sees they're holding something.
        if (!handedness || handedness === ARM) {
            line.visible = false;
            continue;
        }
        line.visible = true;

        // Dragging: ray follows the controller, no raycast needed
        if (dragState.controller === ctrl) {
            line.scale.z = RAY_IDLE_LEN_M * 0.6;
            line.material.color.setHex(HANDLE_COLOR_GRAB);
            continue;
        }

        _tmpMat.identity().extractRotation(ctrl.matrixWorld);
        raycaster.ray.origin.setFromMatrixPosition(ctrl.matrixWorld);
        raycaster.ray.direction.set(0, 0, -1).applyMatrix4(_tmpMat);

        const hits = raycaster.intersectObjects(allTargets, false);
        if (hits.length > 0) {
            const hit = hits[0];
            // Was it a drag handle?
            const handleEntry = dragHandles.find(h => h.handle === hit.object);
            if (handleEntry) {
                hoveredHandle = handleEntry;
                line.scale.z = hit.distance;
                line.material.color.setHex(HANDLE_COLOR_HOVER);
                continue;
            }
            // Was it the collapsed STOP button?
            if (hit.object === stopButtonMesh) {
                _hoveredMeshIsStopButton = true;
                drawStopButton(true);
                line.scale.z = hit.distance;
                line.material.color.setHex(RAY_COLOR_HOVER);
                continue;
            }
            // Otherwise a regular menu button
            const name = hit.object.userData.buttonName;
            const btn = buttons[name];
            if (btn) {
                rawHitButton = btn;       // debounced into hoveredButton below
                line.scale.z = hit.distance;
                line.material.color.setHex(RAY_COLOR_HOVER);
                continue;
            }
        }
        // No hit: restore default-length idle-colored ray
        line.scale.z = RAY_IDLE_LEN_M;
        line.material.color.setHex(RAY_COLOR_IDLE);
    }

    // Commit the (debounced) button highlight from this frame's raw hit.
    _settleHover(rawHitButton, performance.now());

    // Repaint the collapsed STOP idle state if we didn't hover it this frame
    if (!_hoveredMeshIsStopButton && stopButtonMesh.visible) {
        drawStopButton(false);
    }
}

// Once the current status text has fully faded we stop re-uploading the
// (now-blank) texture every frame. drawStatus is called every frame from
// updateFromSnapshot to animate the fade, but past alpha 0 there's nothing
// left to animate -- without this flag a blank status strip would keep
// uploading a cleared 800x90 texture forever, for no visible benefit.
let _statusFadeDone = false;

function drawStatus(text) {
    if (text !== lastStatusText) {
        lastStatusText = text;
        lastStatusAt = performance.now();
        _statusFadeDone = false;
    }
    if (_statusFadeDone) return;   // fully faded + already cleared once; nothing to do

    const ctx = statusCanvas.getContext('2d');
    const W = statusCanvas.width, H = statusCanvas.height;
    ctx.clearRect(0, 0, W, H);
    if (!text) {
        statusTex.needsUpdate = true;
        _statusFadeDone = true;     // blank: one upload, then stop
        return;
    }
    // Fade alpha over STATUS_FADE_MS
    const age = performance.now() - lastStatusAt;
    const alpha = Math.max(0, 1 - age / STATUS_FADE_MS);
    if (alpha <= 0) {
        statusTex.needsUpdate = true;   // final cleared upload
        _statusFadeDone = true;         // ...then stop until the text changes
        return;
    }
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
    // Update text + restart fade timer. Force a re-render even if the text
    // is identical to what just faded (re-issuing the same message should
    // flash it again).
    lastStatusText = text;
    lastStatusAt = performance.now();
    _statusFadeDone = false;
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

function curlFromDistance(dist, fingerIdx, maxArr) {
    if (dist > maxArr[fingerIdx]) maxArr[fingerIdx] = dist;
    const maxD = maxArr[fingerIdx];
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
        out.push(curlFromDistance(d, i, fingerMaxExtendedReal));
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
        out.push(curlFromDistance(d, i, fingerMaxExtendedPred));
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
    // user at session start (or restore previously-saved layout). After this
    // they're world-anchored -- they don't follow the head (head-locked
    // panels make people queasy).
    const viewerPose = frame.getViewerPose(refSpace);
    if (!viewerPose) return;
    const view = viewerPose.views[0];
    const m = new THREE.Matrix4().fromArray(view.transform.matrix);
    const headPos = new THREE.Vector3().setFromMatrixPosition(m);
    const headFwd = new THREE.Vector3(0, 0, -1).applyMatrix4(
        new THREE.Matrix4().extractRotation(m));

    // Slate position is always fresh per-session -- it pops up in front of
    // wherever the user is right now when REC fires, regardless of where
    // the panels live. So we always set it here.
    slateMesh.position.copy(headPos).addScaledVector(headFwd, 0.55);
    slateMesh.lookAt(headPos);

    // If a saved layout exists from a prior drag (or prior session), apply
    // it and skip the default positioning. The user's preferred field-
    // capture workstation comes back the way they left it.
    const saved = loadPanelLayout();
    if (saved && _applyPoseJSON(infoGroup, saved.info) && _applyPoseJSON(menuRoot, saved.menu)) {
        placed = true;
        return;
    }

    // Default positioning: infoGroup (heatmap + header + compare) in front
    // of the user, slightly below eye height. infoGroup's local origin is
    // at the heatmap center; header and compare positions are local offsets
    // set at init. After this initial placement the user can drag the whole
    // group via its handle to wherever fits their workspace.
    const heatPos = headPos.clone().addScaledVector(headFwd, HEATMAP_FORWARD_M);
    heatPos.y -= 0.10 * UI_SCALE;
    infoGroup.position.copy(heatPos);
    infoGroup.lookAt(headPos);
    infoGroup.scale.setScalar(UI_SCALE);

    // Place menuRoot (menu panel + status strip). Same forward distance but
    // dropped further so it sits below the data display. Tilted toward the
    // head for ergonomic button-reach. menuPanel + statusMesh local offsets
    // were set at init.
    const menuPos = heatPos.clone().add(new THREE.Vector3(0, -MENU_OFFSET_DOWN * UI_SCALE, 0));
    menuRoot.position.copy(menuPos);
    menuRoot.lookAt(headPos);
    menuRoot.rotateX(THREE.MathUtils.degToRad(MENU_TILT_DEG));
    menuRoot.scale.setScalar(UI_SCALE);

    // statusMesh is now a child of menuRoot with a fixed local offset --
    // no separate positioning needed here. It moves and tilts with the menu.

    placed = true;
}

// ---------------------------------------------------------------------------
// WebSockets
// ---------------------------------------------------------------------------

let questWs = null;
let liveWs = null;
let latestSnapshot = null;
let latestSnapshotAt = 0;       // performance.now() of the last /ws/live message
// "Want open" flags. The auto-reconnect on a socket's `close` event must NOT
// fire when WE deliberately closed it (on sessionend). Without these flags,
// closing the sockets at sessionend triggers their close handlers, which
// reconnect 1.5s later -- so the page keeps hammering /ws/quest + /ws/live
// forever after you exit VR, and re-entering VR stacks another reconnect
// loop on top. The flag lets close handlers distinguish a network blip
// (reconnect) from an intentional teardown (stay closed).
let _questWsWantOpen = false;
let _liveWsWantOpen = false;

function wsURL(path) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}${path}`;
}

function connectQuestWS() {
    _questWsWantOpen = true;
    questWs = new WebSocket(wsURL('/ws/quest'));
    questWs.addEventListener('close', () => {
        // Reconnect with backoff so a network blip doesn't lose the session,
        // but only if we still want this socket open (not after teardown).
        if (_questWsWantOpen) setTimeout(connectQuestWS, 1500);
    });
    questWs.addEventListener('error', () => { try { questWs.close(); } catch (e) {} });
}

function connectLiveWS() {
    _liveWsWantOpen = true;
    liveWs = new WebSocket(wsURL('/ws/live'));
    liveWs.addEventListener('message', (ev) => {
        try { latestSnapshot = JSON.parse(ev.data); latestSnapshotAt = performance.now(); } catch (e) {}
    });
    liveWs.addEventListener('close', () => {
        if (_liveWsWantOpen) setTimeout(connectLiveWS, 1500);
    });
    liveWs.addEventListener('error', () => { try { liveWs.close(); } catch (e) {} });
}

// Intentional teardown: clear the want-open flag first so the close handler
// does NOT schedule a reconnect, then close + drop the reference.
function closeQuestWS() {
    _questWsWantOpen = false;
    try { questWs && questWs.close(); } catch (e) {}
    questWs = null;
}

function closeLiveWS() {
    _liveWsWantOpen = false;
    try { liveWs && liveWs.close(); } catch (e) {}
    liveWs = null;
}

// ---------------------------------------------------------------------------
// Per-frame: capture hand, detect pinch, raycast button, paint heatmap
// ---------------------------------------------------------------------------

function jointPose(frame, refSpace, hand, name) {
    const j = hand.get(name);
    if (!j) return null;
    return frame.getJointPose(j, refSpace);
}

function shouldReport(timestampMs) {
    // Throttle /ws/quest sends to REPORT_HZ and require an open socket. Called
    // once per frame so that, in two-hand mode, both hands share the same tick
    // (rather than the second hand being throttled out by the first).
    if (!questWs || questWs.readyState !== WebSocket.OPEN) return false;
    if (timestampMs - lastReportAt < 1000 / REPORT_HZ) return false;
    lastReportAt = timestampMs;
    return true;
}

function sendHand(frame, refSpace, hand, handedness, timestampMs) {
    // Emit ALL joints in canonical JOINT_NAMES order, every frame, even when
    // a joint's pose is momentarily unavailable. This keeps the flattened
    // `values` array a FIXED length with STABLE slot meaning -- critical
    // because the server writes these straight into CSV columns. The old
    // code skipped missing joints, which (a) made the payload length vary
    // frame to frame -> ragged CSV, and worse (b) shifted every later joint
    // into the wrong column when a middle joint dropped -> silently
    // misaligned ground-truth labels. For an unavailable joint we send a
    // zero position + identity quaternion and valid:false, so downstream can
    // filter it (the validity is preserved in the JSONL sidecar; the CSV
    // itself carries the zeros). Only when NO joint is available do we drop
    // the whole frame -- there's nothing to pair.
    //
    // device_id = quest-<handedness> so the PC recorder can route each hand to
    // its own side (two-hand bilateral capture: left band <- quest-left, etc.).
    const joints = [];
    let validCount = 0;
    for (const name of JOINT_NAMES) {
        const p = jointPose(frame, refSpace, hand, name);
        if (p) {
            const t = p.transform;
            joints.push({
                name,
                pos: [t.position.x, t.position.y, t.position.z],
                rot: [t.orientation.x, t.orientation.y, t.orientation.z, t.orientation.w],
                radius: p.radius || 0,
                valid: true,
            });
            validCount++;
        } else {
            joints.push({
                name,
                pos: [0, 0, 0],
                rot: [0, 0, 0, 1],
                radius: 0,
                valid: false,
            });
        }
    }
    if (validCount === 0) return;  // hand fully untracked this frame; drop it
    questWs.send(JSON.stringify({
        device_id: `quest-${handedness}`,
        ts: Math.floor(timestampMs),
        handedness,
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
const _ghostPredQuatInv = new THREE.Quaternion();
const _ghostRealQuat = new THREE.Quaternion();
const _ghostDeltaQuat = new THREE.Quaternion();

// The predicted joint vector for the band tagged with `side` (left/right): that
// band's device_id -> snap.inference.by_device. Null if the band isn't tagged,
// isn't predicting, or its prediction is stale.
function predForSide(snap, side) {
    const band = (snap && snap.devices || []).find(
        (d) => d.device_type === 'flexgrid' && d.role === side);
    const byDev = snap && snap.inference && snap.inference.by_device;
    return (band && byDev && byDev[band.device_id]) || null;
}

function updateGhostHand(meshes, predValues, realWristPos, realWristQuat) {
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
        for (const m of meshes.values()) m.visible = false;
        return;
    }
    _ghostPredWristPos.set(predValues[0], predValues[1], predValues[2]);
    _ghostRealWristPos.set(realWristPos.x, realWristPos.y, realWristPos.z);

    let useRotation = false;
    if (realWristQuat) {
        const qx = predValues[3], qy = predValues[4], qz = predValues[5], qw = predValues[6];
        // Guard a degenerate (near-zero) predicted wrist quaternion: a poorly
        // trained model can output one, and inverting it divides by ~zero ->
        // NaN -> the whole ghost hand jumps to NaN positions. When that
        // happens we fall back to position-only alignment (still useful).
        const qLenSq = qx * qx + qy * qy + qz * qz + qw * qw;
        if (qLenSq > 1e-6) {
            _ghostPredQuat.set(qx, qy, qz, qw).normalize();
            _ghostRealQuat.set(realWristQuat.x, realWristQuat.y, realWristQuat.z, realWristQuat.w);
            // delta = real * inverse(pred). Apply to (pred_pos - pred_wrist_pos)
            // to land in the real-wrist's frame. Uses a dedicated inverse temp
            // (no per-frame allocation).
            _ghostPredQuatInv.copy(_ghostPredQuat).invert();
            _ghostDeltaQuat.copy(_ghostRealQuat).multiply(_ghostPredQuatInv);
            useRotation = true;
        }
    }

    let i = 0;
    for (const [, mesh] of meshes) {
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
        if (uiState.pinchStart === 0) {
            uiState.pinchStart = timestampMs;
            uiState.pinchToggled = false;   // fresh hold
        }
        const held = timestampMs - uiState.pinchStart;
        const progress = Math.min(1.0, held / PINCH_HOLD_MS);
        pinchIndicator.visible = true;
        pinchIndicator.material.opacity = 0.3 + 0.7 * progress;
        // Fire exactly once per hold. Previously this used pinchStart =
        // -Infinity + a 1.5s cooldown to "require release", but that made
        // `held` Infinity so a CONTINUOUS hold re-toggled every 1.5s. The
        // pinchToggled flag enforces a real release (a non-pinching frame
        // clears it below) before the next toggle can fire.
        if (!uiState.pinchToggled && held >= PINCH_HOLD_MS) {
            uiState.pinchToggled = true;
            toggleRecording();
        }
    } else {
        uiState.pinchStart = 0;
        uiState.pinchToggled = false;   // released -> next hold can toggle again
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

// A small IMU tilt-band widget (accel-tilt orientation feedback). One per
// FlexGrid band, parented under the band's group below its heatmap.
function makeImuBand() {
    const band = new THREE.Mesh(
        new THREE.BoxGeometry(0.10, 0.004, 0.026),
        new THREE.MeshBasicMaterial({ color: 0x3b82f6 }));
    band.add(new THREE.LineSegments(
        new THREE.EdgesGeometry(band.geometry),
        new THREE.LineBasicMaterial({ color: 0x93c5fd })));
    const edge = new THREE.Mesh(
        new THREE.BoxGeometry(0.006, 0.007, 0.03),
        new THREE.MeshBasicMaterial({ color: 0xfbbf24 }));   // +X end marker
    edge.position.set(0.05, 0, 0);
    band.add(edge);
    return band;
}

// --- Orientation gizmo (locked board #0206 convention) ----------------------
// A 2D XYZ axis gizmo so the VR orientation widget is pixel-consistent with the
// phone's Compose Canvas gizmo. Convention (locked #0206, phone signed off
// #0222): world frame +X right / +Y up / +Z toward viewer; axis colors X red
// ef4444 / Y green 22c55e / Z blue 3b82f6; orthographic -Z projection (drop the
// rotated z, screen y points up); vectors from center, len 0.4*min(w,h);
// painter-sorted far-to-near by projected z so the nearer axis draws on top.
// Fed the accel-tilt orientation (interim, correct pitch/roll, no yaw) until
// firmware states the gyro scale + mounting (#0200); the SAME quaternion will
// then come from the Madgwick filter (fusion.py) with NO change to this draw.
// THIS is the JS reference phone mirrors in Kotlin (#0206 / #0222).
const _GIZMO_AXES = [
    { v: [1, 0, 0], color: '#ef4444', label: 'X' },
    { v: [0, 1, 0], color: '#22c55e', label: 'Y' },
    { v: [0, 0, 1], color: '#3b82f6', label: 'Z' },
];
const _gizV = new THREE.Vector3();
function drawOrientationGizmo(ctx, w, h, quat) {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = 'rgba(13,17,23,0.82)';
    ctx.fillRect(0, 0, w, h);
    const cx = w / 2, cy = h / 2, len = 0.4 * Math.min(w, h);
    // Rotate each unit axis by the orientation, then project orthographically
    // onto the -Z view: screen x = +model x, screen y = -model y (so +Y is UP).
    const proj = _GIZMO_AXES.map((ax) => {
        _gizV.set(ax.v[0], ax.v[1], ax.v[2]).applyQuaternion(quat);
        return { ax, sx: cx + _gizV.x * len, sy: cy - _gizV.y * len, z: _gizV.z };
    });
    proj.sort((a, b) => a.z - b.z);          // far (-z) first, near (+z) on top
    ctx.lineWidth = Math.max(3, len * 0.06);
    ctx.lineCap = 'round';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = 'bold ' + Math.round(len * 0.22) + 'px system-ui';
    for (const p of proj) {
        ctx.globalAlpha = p.z >= 0 ? 1 : 0.5;   // dim an axis pointing away
        ctx.strokeStyle = p.ax.color;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(p.sx, p.sy);
        ctx.stroke();
        ctx.fillStyle = p.ax.color;
        ctx.fillText(p.ax.label, cx + (p.sx - cx) * 1.13, cy + (p.sy - cy) * 1.13);
    }
    ctx.globalAlpha = 1;
}

// Small per-band canvas panel that renders the orientation gizmo above.
function makeGizmoPanel() {
    const canvas = document.createElement('canvas');
    canvas.width = 96; canvas.height = 96;
    const ctx = canvas.getContext('2d');
    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    const mesh = new THREE.Mesh(
        new THREE.PlaneGeometry(BAND_H, BAND_H),     // small square panel
        new THREE.MeshBasicMaterial({ map: tex, transparent: true,
                                      side: THREE.DoubleSide }));
    drawOrientationGizmo(ctx, canvas.width, canvas.height, new THREE.Quaternion());
    tex.needsUpdate = true;
    return { mesh, canvas, ctx, tex };
}

// Lazily create the per-band group (heatmap mesh + canvas + IMU widget) for a
// device_id, added to bandRow. Reused on subsequent frames.
function ensureBand(deviceId) {
    let rec = bands.get(deviceId);
    if (rec) return rec;
    const group = new THREE.Group();
    const canvas = document.createElement('canvas');
    canvas.width = 256; canvas.height = 96;
    const ctx = canvas.getContext('2d');
    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(BAND_W, BAND_H),
        new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide }));
    group.add(mesh);
    // Orientation widget: the locked #0206 XYZ axis gizmo (pixel-consistent with
    // the phone) fed by the band's accel-tilt. Replaces the older tilting box.
    const giz = makeGizmoPanel();
    giz.mesh.position.set(0, -(BAND_H * 0.5 + 0.04), 0.01);
    group.add(giz.mesh);
    bandRow.add(group);
    rec = { group, mesh, canvas, ctx, tex, giz, gizQuat: new THREE.Quaternion(),
            vmax: 100, lastSig: '' };
    bands.set(deviceId, rec);
    return rec;
}

// Ordered FlexGrid bands from the snapshot (left, right, labeler, then by id).
// role/subscribed are joined onto each device server-side (state.py _snapshot).
function getBands(snap) {
    const fgs = (snap.devices || []).filter(
        (d) => d.device_type === 'flexgrid' && d.matrix && d.matrix.length);
    fgs.sort((a, b) =>
        ((BAND_ROLE_ORDER[a.role] ?? 9) - (BAND_ROLE_ORDER[b.role] ?? 9))
        || (a.device_id < b.device_id ? -1 : 1));
    return fgs;
}

// Draw each band's heatmap + IMU, lay them out side by side, and hide groups
// whose device vanished from the snapshot (device-lifecycle cleanup).
function reconcileBands(list) {
    const n = list.length;
    const seen = new Set();
    list.forEach((d, i) => {
        seen.add(d.device_id);
        const rec = ensureBand(d.device_id);
        rec.group.position.x = (i - (n - 1) / 2) * (BAND_W + BAND_GAP);
        rec.group.visible = true;
        const role = (d.role || '').toUpperCase() || '?';
        drawBandHeatmap(rec, d.matrix, role + '  ' + Math.round(d.hz || 0) + 'Hz');
        if (d.imu) updateBandImu(rec, d.imu);
    });
    for (const [id, rec] of bands) {
        if (!seen.has(id)) rec.group.visible = false;
    }
}

// Physical sources that have a battery + signal: the FlexGrid bands + the LASK5
// labeler. (The Quest hand is the headset; its health shows in debug mode.)
function getStatusDevices(snap) {
    return (snap.devices || []).filter(
        (d) => d.device_type === 'flexgrid' || d.device_type === 'lask5');
}

function _batteryColor(pct, stale) {
    if (stale || pct == null) return '#6b7280';   // gray: unknown / stale telemetry
    if (pct >= 70) return '#22c55e';
    if (pct >= 30) return '#f59e0b';
    return '#ef4444';
}

// One row per source: freshness dot + side + battery (V/%) + Hz + subscribe state,
// so "are both bracelets alive and streaming" reads at a glance. Re-uploads the
// texture only when something changes.
function drawBandStatus(devices) {
    const key = devices.map((d) => {
        const s = d.status || {};
        const f = d.last_seen_age == null ? 'o'
            : d.last_seen_age < 2 ? 'f' : d.last_seen_age < 5 ? 's' : 'o';
        return [d.device_id, d.role, Math.round(d.hz || 0), s.pct,
                Math.round((s.vbat || 0) * 100), f, d.subscribed ? 1 : 0].join(',');
    }).join('|');
    if (key === _bandStatusKey) return;
    _bandStatusKey = key;

    const ctx = bandStatusCtx, W = bandStatusCanvas.width, H = bandStatusCanvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = 'rgba(13,17,23,0.82)';
    ctx.fillRect(0, 0, W, H);
    ctx.textBaseline = 'middle';
    if (!devices.length) {
        ctx.fillStyle = '#8b96a8'; ctx.font = '26px system-ui'; ctx.textAlign = 'left';
        ctx.fillText('no bands streaming', 16, H / 2);
        bandStatusTex.needsUpdate = true;
        return;
    }
    const rowH = Math.min(64, H / devices.length);
    devices.forEach((d, i) => {
        const y = i * rowH + rowH / 2;
        const s = d.status || {};
        const role = (d.role || d.device_type || '?').toString().toUpperCase().slice(0, 5);
        const telemetryStale = d.status_age == null || d.status_age > 10;
        const fresh = d.last_seen_age != null && d.last_seen_age < 2;
        const recent = d.last_seen_age != null && d.last_seen_age < 5;
        ctx.beginPath();
        ctx.arc(24, y, 9, 0, Math.PI * 2);
        ctx.fillStyle = fresh ? '#22c55e' : recent ? '#f59e0b' : '#ef4444';
        ctx.fill();
        ctx.fillStyle = '#e6e9ef';
        ctx.font = 'bold 28px system-ui';
        ctx.textAlign = 'left';
        ctx.fillText(role, 44, y);
        ctx.fillStyle = _batteryColor(s.pct, telemetryStale);
        ctx.font = '26px ui-monospace, monospace';
        const batt = (s.vbat != null ? s.vbat.toFixed(2) + 'V ' : '')
            + (s.pct != null ? s.pct + '%' : '--');
        ctx.fillText(batt, 200, y);
        ctx.fillStyle = (d.hz || 0) > 1 ? '#e6e9ef' : '#ef4444';
        ctx.fillText(Math.round(d.hz || 0) + 'Hz', 470, y);
        if (d.subscribed === false || d.sub_error) {
            ctx.fillStyle = '#ef4444';
            ctx.font = 'bold 22px system-ui';
            ctx.fillText('NO SUB', 610, y);
        }
    });
    bandStatusTex.needsUpdate = true;
}

// The debug overlay: one health line per signal (green dot = good, amber = warming
// / partial, red = bad). Sections are labeled so XR-hand tracking is never
// confused with server-side recording. Pulls live data from the latest snapshot
// + the frame-loop debugState.
function drawDebug(snap) {
    const ctx = debugCtx, W = debugCanvas.width, H = debugCanvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = 'rgba(13,17,23,0.92)';
    ctx.fillRect(0, 0, W, H);
    ctx.textBaseline = 'middle';
    let y = 22;
    const line = (label, ok, val) => {
        ctx.beginPath();
        ctx.arc(20, y, 8, 0, Math.PI * 2);
        ctx.fillStyle = ok === true ? '#22c55e' : ok === false ? '#ef4444' : '#f59e0b';
        ctx.fill();
        ctx.fillStyle = '#e6e9ef';
        ctx.font = '22px ui-monospace, monospace';
        ctx.textAlign = 'left';
        ctx.fillText(label, 40, y);
        if (val != null) {
            ctx.fillStyle = '#9ca3af';
            ctx.textAlign = 'right';
            ctx.fillText(String(val), W - 14, y);
        }
        y += 34;
    };
    const head = (t) => {
        ctx.fillStyle = '#7d8694';
        ctx.font = 'bold 18px system-ui';
        ctx.textAlign = 'left';
        ctx.fillText(t, 14, y);
        y += 26;
    };

    head('XR HAND TRACKING');
    if (BOTH_HANDS) {
        line('left hand', debugState.leftHand, debugState.leftHand ? 'tracked' : 'NOT seen');
        line('right hand', debugState.rightHand, debugState.rightHand ? 'tracked' : 'NOT seen');
    } else {
        const h = debugState.leftHand || debugState.rightHand;
        line('captured hand', h, h ? 'tracked' : 'NOT seen');
    }

    head('BANDS (server)');
    const devs = getStatusDevices(snap || {});
    if (!devs.length) line('no bands streaming', false, '');
    devs.forEach((d) => {
        const fresh = d.last_seen_age != null && d.last_seen_age < 2;
        const role = (d.role || d.device_type || '?').toString().toUpperCase();
        const batt = d.status && d.status.pct != null ? ' ' + d.status.pct + '%' : '';
        line(role + ' ' + Math.round(d.hz || 0) + 'Hz' + batt,
             fresh && (d.hz || 0) > 1,
             d.subscribed === false ? 'NO SUB' : fresh ? 'live' : 'stale');
    });

    head('CAPTURE (server)');
    const rec = snap && snap.recording;
    line('recording', !!rec, rec ? Math.round((rec.match_rate || 0) * 100) + '% match' : 'idle');
    if (rec && rec.label_width_mismatch)
        line('joints dropping', false, rec.label_width_mismatch);
    const inf = snap && snap.inference;
    line('inference', inf && inf.enabled ? inf.status === 'live' : null,
         inf ? inf.status || (inf.enabled ? 'on' : 'off') : 'no model');

    head('LINKS');
    line('live socket', debugState.liveWs, debugState.liveWs ? 'open' : 'closed');
    line('quest socket', debugState.questWs, debugState.questWs ? 'open' : 'closed');
    line('snapshot age', debugState.snapAgeMs < 2000, Math.round(debugState.snapAgeMs) + 'ms');
    if (debugState.vis) line('xr visibility', debugState.vis === 'visible', debugState.vis);

    debugTex.needsUpdate = true;
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

// Paint one band's matrix onto its own canvas, with a per-band auto-scaling vmax
// (a quiet band doesn't get dimmed by a loud one) + a top label strip showing the
// band's side + live Hz, so "which arm + is it responding" reads off the panel.
function drawBandHeatmap(rec, matrix, label) {
    if (!matrix || matrix.length === 0) return;
    const cols = matrix.length;
    const rows = matrix[0].length;
    let m = 1;
    for (let c = 0; c < cols; c++)
        for (let r = 0; r < rows; r++)
            if (matrix[c][r] > m) m = matrix[c][r];
    if (m > rec.vmax) rec.vmax = m;        // auto-scale upward, per band
    const vmax = rec.vmax;

    const sig = matrix[0][0] + ':' + matrix[cols - 1][rows - 1] + ':' + vmax + ':' + label;
    if (sig === rec.lastSig) return;        // cheap redraw skip
    rec.lastSig = sig;

    const ctx = rec.ctx, W = rec.canvas.width, H = rec.canvas.height;
    const LABEL_H = 24;
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, W, H);
    const gridH = H - LABEL_H;
    const cw = W / cols, ch = gridH / rows;
    for (let c = 0; c < cols; c++) {
        for (let r = 0; r < rows; r++) {
            const [R, G, B] = colorRamp(matrix[c][r] / vmax);
            ctx.fillStyle = 'rgb(' + (R | 0) + ',' + (G | 0) + ',' + (B | 0) + ')';
            ctx.fillRect(c * cw, LABEL_H + r * ch, Math.ceil(cw), Math.ceil(ch));
        }
    }
    ctx.fillStyle = '#161b22';
    ctx.fillRect(0, 0, W, LABEL_H);
    ctx.fillStyle = '#e6e9ef';
    ctx.font = 'bold 16px system-ui';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, 6, LABEL_H / 2 + 1);
    rec.tex.needsUpdate = true;
}

// Guard against redundant header re-uploads. While recording the text
// changes every frame (ms clock) so it legitimately redraws; while idle the
// "X Hz · Y Hz" string changes ~1/sec, so without this it would re-upload
// the texture 72x/sec for nothing. Keyed on the full (text, recording,
// quality) tuple.
let _headerLastKey = null;

function drawHeader(text, isRecording, qualityColor) {
    const key = text + '|' + isRecording + '|' + (qualityColor || '');
    if (key === _headerLastKey) return;
    _headerLastKey = key;

    const ctx = headerCanvas.getContext('2d');
    const W = headerCanvas.width, H = headerCanvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = isRecording ? 'rgba(239, 68, 68, 0.95)' : 'rgba(20, 24, 32, 0.85)';
    const pad = 12;
    ctx.fillRect(pad, pad, W - pad * 2, H - pad * 2);

    // Live capture-quality dot on the left (recording only). Green = good
    // match rate, amber = marginal, red = poor, gray = still warming up.
    let textLeftInset = 0;
    if (qualityColor) {
        ctx.fillStyle = qualityColor;
        ctx.beginPath();
        ctx.arc(44, H / 2, 16, 0, Math.PI * 2);
        ctx.fill();
        textLeftInset = 36;   // nudge centered text right so it clears the dot
    }

    // Auto-fit the font so long filenames don't overflow the canvas edges.
    ctx.fillStyle = '#f0f4f8';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    let fontPx = 32;
    const maxTextW = W - pad * 2 - textLeftInset - 24;
    do {
        ctx.font = `bold ${fontPx}px system-ui`;
        if (ctx.measureText(text).width <= maxTextW) break;
        fontPx -= 2;
    } while (fontPx > 16);
    ctx.fillText(text, (W + textLeftInset) / 2, H / 2);
    headerTex.needsUpdate = true;
}

// Tilt the IMU band widget from the FlexGrid's live data.imu. Accel-tilt: the
// normalized accel is gravity-up in the band frame, so the band's normal follows
// it (correct pitch/roll, no gyro-scale dependency; yaw stable). Same axis
// mapping as the desktop imu-viewer for cross-hub consistency (the mapping is the
// one tunable to align with phone, board #0197).
function updateBandImu(rec, imu) {
    if (!rec.giz || !imu) return;
    const a = imu.accel;
    if (!Array.isArray(a) || a.length < 3) return;
    if (Math.hypot(a[0], a[1], a[2]) < 1e-3) return;   // freefall / bad read
    _imuUp.set(a[0], a[2], -a[1]).normalize();          // sensor -> model axes
    _imuTargetQuat.setFromUnitVectors(_imuNormal, _imuUp);
    rec.gizQuat.slerp(_imuTargetQuat, 0.3);             // smooth the jitter
    drawOrientationGizmo(rec.giz.ctx, rec.giz.canvas.width,
                         rec.giz.canvas.height, rec.gizQuat);
    rec.giz.tex.needsUpdate = true;
}

function updateFromSnapshot(snap, timestampMs) {
    if (!snap) return;
    // Paint EVERY FlexGrid band (two-hand mode -> LEFT + RIGHT side by side).
    const bandList = getBands(snap);
    reconcileBands(bandList);
    drawBandStatus(getStatusDevices(snap));   // per-band battery + signal panel
    const fg = bandList[0] || null;   // primary band for the header Hz line below
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
        // Live capture-quality gauge so the operator can tell mid-session
        // whether the data is clean -- without taking the headset off or
        // checking the PC. The dot color tracks the sensor<->label match
        // rate; a "JOINTS DROPPING" suffix warns when partial hand tracking
        // is forcing the server to zero-pad joint columns (label_width_mismatch).
        const mr = rec.match_rate || 0;
        const pct = Math.round(mr * 100);
        const dropping = (rec.label_width_mismatch || 0) > 0;
        const warmingUp = (rec.sensor_frames_seen || 0) < 10;
        let qColor;
        if (warmingUp)       qColor = '#9ca3af';   // gray: not enough frames yet
        else if (mr >= 0.70) qColor = '#22c55e';   // green: good
        else if (mr >= 0.40) qColor = '#f59e0b';   // amber: marginal
        else                 qColor = '#ef4444';   // red: poor pairing
        if (dropping && !warmingUp && qColor === '#22c55e') qColor = '#f59e0b';
        let txt = `REC ${hh}:${mm}:${ss}.${ms} · ${rec.filename}`;
        if (!warmingUp) txt += ` · ${pct}%`;
        if (dropping) txt += ' · JOINTS DROPPING';
        drawHeader(txt, true, qColor);
        if (!uiState.recording) {
            // Rising edge: recording just started -- from the in-VR REC button
            // OR the PC (single / multiband / two-hand, where no VR button is
            // pressed). Fire the sync slate here so the headset's screen
            // recording always gets a frame-accurate pairing point. The
            // timestamp is the hub start (started_at_ms), the same clock as the
            // CSV's ts_hub_ms, so the paired frame maps to an exact row.
            showSyncSlate(rec.filename, rec.started_at_ms || Date.now());
        }
        uiState.recording = true;
    } else {
        const fgHz = fg ? `${fg.hz?.toFixed?.(0) || '0'} Hz` : 'no FlexGrid';
        const questDev = (snap.devices || []).find(d => d.device_type === 'quest_hand');
        const questHz = questDev ? `${questDev.hz?.toFixed?.(0) || '0'} Hz` : 'no Quest';
        drawHeader(`${fgHz} · ${questHz}`, false, null);
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

    // SETUP panel: while open, repaint the title strip every frame so the
    // replay progress line ("replaying <capture> N/T") tracks the live
    // snapshot.replay counter. The capture rows + REPLAY/speed buttons repaint
    // through updateButtonVisual -> customDraw (they own their redraw-skip),
    // so we only need to drive the non-button title here.
    if (configPanel && configPanel.visible) {
        drawConfigTitle();
        // BANDS section: run the squeeze-to-identify detector, then bind the
        // live FlexGrid list onto the band slots + repaint the info labels.
        // The L/R/clear sub-buttons repaint through updateButtonVisual ->
        // customDraw (they own their redraw-skip); drawBandsSection re-binds the
        // device each slot points at (so role badges + active highlight track
        // the latest snapshot) and paints the non-button info labels here.
        updateBandIdentify(snap);
        drawBandsSection(snap);
        // TRAIN + MODELS section: the TRAIN buttons + PICK sub-buttons repaint
        // through updateButtonVisual -> customDraw (the TRAIN spinner key is
        // time-derived so it animates); the model-row info labels are non-buttons,
        // so repaint them here. drawTrainSection covers both cheaply (redraw-skip
        // guards inside each paint fn keep it from re-uploading unchanged textures).
        drawTrainSection(snap);
    } else if (bandIdentifyId) {
        bandIdentifyId = null;   // clear stale highlight when the panel closes
    }
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
        } else if (BOTH_HANDS) {
            // Two-hand mode: start a TRUE bilateral capture from the bands
            // already tagged left/right (POST /api/recording/bilateral, empty
            // body) instead of the single-band path. A 400 means the bands
            // aren't tagged or a quest hand isn't streaming -- surface the
            // server's detail so the user knows to open SETUP and tag L/R.
            const r = await fetch('/api/recording/bilateral', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            if (r.ok) {
                const data = await r.json();
                setStatus(`recording (both): ${data.filename}`);
                // Slate fires on the recording-start edge in updateFromSnapshot.
            } else {
                let detail = `HTTP ${r.status}`;
                try { const err = await r.json(); if (err && err.detail) detail = err.detail; }
                catch (e) { /* non-JSON error body */ }
                setStatus(`bilateral failed: ${detail}`.slice(0, 80));
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
                // The sync slate fires on the recording-start edge in
                // updateFromSnapshot (covers PC-started captures too), so it is
                // not triggered here -- that would double-fire it.
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

// Pick the capture(s) to train on: the active session's captures if a session
// is running, else the most recent capture only. The fallback keeps the "tap
// TRAIN right after a recording" flow alive even without sessions. Returns a
// (possibly empty) array of capture filenames. Shared by runTrain (pooled) and
// the TRAIN BOTH path so both train on the SAME capture(s).
async function pickTrainCaptures() {
    let captureNames = [];
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
    return captureNames;
}

async function runTrain() {
    if (uiState.training) {
        setStatus('training already in flight -- wait for it to finish');
        return;
    }
    if (uiState.recording) {
        setStatus('stop recording before training');
        return;
    }
    let captureNames = [];
    try {
        captureNames = await pickTrainCaptures();
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

function onXRFrameImpl(timestamp, frame) {
    const session = renderer.xr.getSession();
    const refSpace = renderer.xr.getReferenceSpace();
    if (!session || !refSpace) return;

    // If the XR session is paused (Quest's boundary redraw, system menu,
    // notification overlay) skip everything that depends on live poses --
    // they can return null or stale data. Keep rendering the scene so the
    // user still sees our panels when the pause clears or for partial
    // visibility, but don't push captured joints to /ws/quest or update
    // hand visualizers. The visibilitychange handler already set placed=
    // false at the moment of resume, so the next non-paused frame will
    // re-anchor everything to the user's new position automatically.
    if (xrPaused) {
        renderer.render(scene, camera);
        return;
    }

    if (!placed) placeAnchors(frame, refSpace);

    // Collect both hands by their actual handedness.
    let leftHand = null, rightHand = null;
    for (const input of session.inputSources) {
        if (!input.hand) continue;
        if (input.handedness === 'left')       leftHand = input.hand;
        else if (input.handedness === 'right') rightHand = input.hand;
    }
    debugState.leftHand = !!leftHand;
    debugState.rightHand = !!rightHand;

    // capturedHand/offHand drive the single-hand UI (compare, ghost, menu) later
    // this frame; they stay null in two-hand mode so that logic skips cleanly and
    // the rest of the frame (heatmap, render) still runs.
    let capturedHand = null, offHand = null;
    if (BOTH_HANDS) {
        // Two-hand capture: stream BOTH hands as quest-left / quest-right (one
        // throttle tick shared) so the PC recorder matches each band to its own
        // hand. Visualize both; the in-VR menu/pinch is unused (record from PC).
        if (shouldReport(timestamp)) {
            if (leftHand)  sendHand(frame, refSpace, leftHand,  'left',  timestamp);
            if (rightHand) sendHand(frame, refSpace, rightHand, 'right', timestamp);
        }
        if (leftHand) updateHandVisualizer(frame, refSpace, leftHand, armJointMeshes);
        else hideHandVisualizer(armJointMeshes);
        if (rightHand) updateHandVisualizer(frame, refSpace, rightHand, offHandJointMeshes);
        else hideHandVisualizer(offHandJointMeshes);
        pinchIndicator.visible = false;
    } else {
        capturedHand = (ARM === 'left') ? leftHand : rightHand;
        offHand      = (ARM === 'left') ? rightHand : leftHand;
        if (capturedHand) {
            if (shouldReport(timestamp)) sendHand(frame, refSpace, capturedHand, ARM, timestamp);
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
    }

    // Predicted ghost hand(s). Two-hand mode draws a ghost next to EACH real
    // hand from that hand's band prediction (snap.inference.by_device keyed by
    // role); single-hand draws one for the captured arm + the REAL-vs-PRED bars.
    const drawGhost = (side, hand) => {
        const pv = uiState.inferenceEnabled ? predForSide(latestSnapshot, side) : null;
        const wp = (hand && pv) ? jointPose(frame, refSpace, hand, 'wrist') : null;
        updateGhostHand(ghosts[side].meshes, pv,
                        wp ? wp.transform.position : null,
                        wp ? wp.transform.orientation : null);
    };
    if (BOTH_HANDS) {
        compareMesh.visible = false;
        drawGhost('left', leftHand);
        drawGhost('right', rightHand);
    } else {
        const side = (ARM === 'left') ? 'left' : 'right';
        const other = side === 'left' ? 'right' : 'left';
        for (const m of ghosts[other].meshes.values()) m.visible = false;
        // Per-band prediction for the captured arm, falling back to the single
        // last prediction when the band isn't role-tagged.
        const predValues = (uiState.inferenceEnabled
            ? (predForSide(latestSnapshot, side)
               || (latestSnapshot && latestSnapshot.inference
                   && latestSnapshot.inference.piston_values))
            : null) || null;
        const showCompare = capturedHand && predValues;
        compareMesh.visible = !!showCompare;
        if (showCompare) {
            const real = realCurls(frame, refSpace, capturedHand);
            drawCompare(real, predictedCurls(predValues));
            const wp = jointPose(frame, refSpace, capturedHand, 'wrist');
            updateGhostHand(ghosts[side].meshes, predValues,
                            wp ? wp.transform.position : null,
                            wp ? wp.transform.orientation : null);
        } else {
            for (const m of ghosts[side].meshes.values()) m.visible = false;
        }
    }

    updateDrag();            // keep any actively-dragged group glued to the controller
    updateRaycast();
    updateButtonVisual();
    updateFromSnapshot(latestSnapshot, timestamp);
    updateSlateVisibility();
    setRecordingCollapsedUI(uiState.recording);   // swap menu <-> STOP every frame

    // Debug overlay: refresh the link/visibility signals + redraw at ~4 Hz.
    debugMesh.visible = uiState.debugOn;
    if (uiState.debugOn) {
        debugState.liveWs = !!liveWs && liveWs.readyState === WebSocket.OPEN;
        debugState.questWs = !!questWs && questWs.readyState === WebSocket.OPEN;
        debugState.vis = session.visibilityState || '';
        debugState.snapAgeMs = latestSnapshotAt ? performance.now() - latestSnapshotAt : 99999;
        if (timestamp - _debugLastDraw > 250) {
            _debugLastDraw = timestamp;
            drawDebug(latestSnapshot);
        }
    }

    renderer.render(scene, camera);
}

// Resilient frame-loop wrapper. A single throwing frame (malformed snapshot,
// an unexpected null pose, a bad predicted vector) must NOT kill the whole
// XR session -- on a field-capture rig that would freeze the headset mid-
// recording and lose work that can't easily be redone. We catch, rate-limit
// a console.error so the bug is still visible, and ALWAYS render so the view
// stays live and the user can still reach EXIT / STOP. The error is not
// swallowed silently -- it's logged, just not fatal.
let _frameErrCount = 0;
let _frameErrLastLogged = 0;
function onXRFrame(timestamp, frame) {
    try {
        onXRFrameImpl(timestamp, frame);
    } catch (e) {
        _frameErrCount++;
        // Log the first error immediately, then at most once every 2s, with
        // a running count so a persistent per-frame error is obvious but
        // doesn't flood the console at 72fps.
        const now = performance.now();
        if (_frameErrCount === 1 || now - _frameErrLastLogged > 2000) {
            _frameErrLastLogged = now;
            console.error(`[openmuscle-vr] frame error #${_frameErrCount}:`, e);
        }
        try { renderer.render(scene, camera); } catch (e2) { /* last resort */ }
    }
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
        xrPaused = false;
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

        // Pause/resume detection. visibilityState is one of:
        //   'visible'         -- normal operation
        //   'visible-blurred' -- still drawing but system UI is on top
        //                        (boundary redraw, universal menu, notifications)
        //   'hidden'          -- not rendering at all (headset off)
        // While anything other than 'visible' we should NOT capture-and-send
        // since poses can return stale or null data and would corrupt the
        // recorder timeline. On return to 'visible' we re-anchor because the
        // user's world position may have shifted (e.g., they walked over to
        // redraw their boundary).
        session?.addEventListener('visibilitychange', () => {
            const state = session.visibilityState;
            const nowPaused = state !== 'visible';
            const wasPaused = xrPaused;
            xrPaused = nowPaused;
            console.log('[openmuscle-vr] XR visibility:', state,
                        '(paused:', nowPaused, ')');
            if (nowPaused && !wasPaused) {
                xrPausedAt = performance.now();
                // Release any in-progress drag -- selectend won't fire while
                // the system UI is on top, so the drag would otherwise be
                // stuck following a frozen controller through the pause.
                cancelDrag();
                setStatus(`session paused (${state}) -- boundary redraw or system UI`);
            } else if (!nowPaused && wasPaused) {
                const heldMs = Math.round(performance.now() - xrPausedAt);
                setStatus(`session resumed after ${(heldMs / 1000).toFixed(1)}s -- re-anchoring UI`);
                placed = false;   // re-run placeAnchors with current head pose
            }
        });

        connectQuestWS();
        connectLiveWS();
        renderer.setAnimationLoop(onXRFrame);
    });
    renderer.xr.addEventListener('sessionend', async () => {
        renderer.setAnimationLoop(null);
        document.getElementById('landing').style.display = '';
        renderer.domElement.style.display = 'none';
        // If a recording was active when the session ended, stop it server-
        // side so the CSV closes cleanly. Without this the server's
        // ActiveCapture stays open and any future REC press would fail with
        // "Already recording" -- the operator would have to use the desktop
        // UI to recover. Fire-and-forget; if the server is unreachable
        // (e.g., we lost Wi-Fi simultaneously) just log it.
        if (uiState.recording) {
            try {
                const r = await fetch('/api/recording', { method: 'DELETE' });
                if (r.ok) {
                    console.log('[openmuscle-vr] auto-stopped active recording on sessionend');
                } else {
                    // 400 here usually means the server already thinks it's
                    // not recording (state drifted). Log it honestly rather
                    // than claiming success, so a confused capture is
                    // debuggable from the console after the fact.
                    console.warn('[openmuscle-vr] sessionend auto-stop returned HTTP',
                                 r.status, '-- recording may not have closed cleanly');
                }
            } catch (e) {
                console.warn('[openmuscle-vr] failed to auto-stop recording on sessionend:', e);
            }
        }
        // Intentional teardown -- close via the helpers so the reconnect
        // loop actually stops (clearing the want-open flag first).
        closeQuestWS();
        closeLiveWS();
        xrPaused = false;
    });
}

(async function main() {
    await preflightChecks();
    bootVRButton();
})();

// OpenMuscle desktop 3D hand viewer.
//
// Renders quest_hand joint data as a 3D skeleton in the Studio "Live" stage,
// replacing the LASK5 4-piston comparator (which shows zeros for a hand
// label source). Shows the REAL captured hand and, when a quest-trained
// model is running, the model's PREDICTED hand overlaid -- the desktop
// counterpart to the VR ghost hand.
//
// Loaded as an ES module from index.html; exposes a small imperative API on
// window.OMHandViewer so the plain (non-module) app.js can drive it:
//     OMHandViewer.init(containerEl)
//     OMHandViewer.updateHand(side, realFlat, predFlat)
//     OMHandViewer.update(realFlat, predFlat)   // back-compat, right slot
//     OMHandViewer.setVisible(bool)
//     OMHandViewer.snapshot()                   // PNG data URL, all views
//     OMHandViewer.isReady()
//
// PER-HAND SUBVIEWS (Tory: "currently it spins both hands... maybe two
// windows for each and the ability to rotate and zoom 3d. 1 and two handed
// version too"): the old design put both hands in one scene under a single
// always-auto-spinning orbit camera with no zoom. Now each VISIBLE hand gets
// its own sub-viewport inside the container: two side by side when both
// hands stream, one full width when only one does, re-splitting live as
// hands appear and go stale. Each view has an independent camera with
// drag-rotate, wheel zoom, and double-click reset. Auto-rotate is OFF by
// default; an untouched view drifts very slowly (so it still reads as 3D),
// and the first interaction in a view kills its drift for the session.
//
// Implementation choice: ONE WebGLRenderer split with setViewport/setScissor
// per view, not one renderer per hand. A single GL context (browsers cap
// contexts per page), a single RAF loop, and snapshot() stays a one-canvas
// toDataURL that captures every view exactly as displayed, no compositing.
//
// Both hands are transformed into WRIST-LOCAL space (subtract the wrist
// position, rotate by the inverse wrist orientation) before drawing, so the
// hand always appears in a canonical palm orientation regardless of how it
// was held, and REAL vs PRED is a direct shape comparison. (This is the same
// wrist-relative idea tracked for training in issue #2, used here purely for
// visualization.)

import * as THREE from 'three';

// Canonical WebXR hand joint order (25 joints). Index i in a flat values
// array occupies [i*7 .. i*7+6] = px,py,pz, rx,ry,rz,rw.
const N_JOINTS = 25;
const FLOATS_PER_JOINT = 7;

// Bone connectivity as [parentIdx, childIdx] pairs. Wrist = 0; then 4 thumb
// joints (1..4), then 5 each for index/middle/ring/pinky.
const BONES = [
    // thumb
    [0, 1], [1, 2], [2, 3], [3, 4],
    // index
    [0, 5], [5, 6], [6, 7], [7, 8], [8, 9],
    // middle
    [0, 10], [10, 11], [11, 12], [12, 13], [13, 14],
    // ring
    [0, 15], [15, 16], [16, 17], [17, 18], [18, 19],
    // pinky
    [0, 20], [20, 21], [21, 22], [22, 23], [23, 24],
];

// Fingertip joint indices, for slightly larger tip markers.
const TIPS = new Set([4, 9, 14, 19, 24]);

const COLOR_REAL = 0x34d399;   // emerald
const COLOR_PRED = 0xfbbf24;   // amber

// Per-view camera defaults and limits.
const DEFAULT_YAW = 0.6;
const DEFAULT_PITCH = -0.25;
const ZOOM_MIN = 0.35;          // wheel zoom clamps AROUND the fit distance,
const ZOOM_MAX = 4.0;           // so "sensible" tracks whatever hand size fit
const IDLE_DRIFT = 0.05;        // rad/s pre-interaction drift (vs the old
                                // 0.005 rad/FRAME spin Tory complained about)
const REFRAME_FRAC = 0.15;      // material size change that triggers a refit

const VIEW_SIDES = ['left', 'right'];

let renderer = null, container = null;
let raf = null;
let visible = false;
let lastT = 0;                      // RAF timestamp for time-based drift
let dragView = null, lastX = 0, lastY = 0;
const views = { left: null, right: null };

// One reusable hand rig = 25 joint spheres + bone line-segments.
function makeHandRig(color, opacity) {
    const group = new THREE.Group();
    const jointMat = new THREE.MeshBasicMaterial({
        color, transparent: opacity < 1, opacity,
    });
    const tipGeo = new THREE.SphereGeometry(0.007, 10, 8);
    const jointGeo = new THREE.SphereGeometry(0.0045, 8, 6);
    const joints = [];
    for (let i = 0; i < N_JOINTS; i++) {
        const m = new THREE.Mesh(TIPS.has(i) ? tipGeo : jointGeo, jointMat);
        group.add(m);
        joints.push(m);
    }
    // Bones: one BufferGeometry with 2 vertices per bone, updated each frame.
    const positions = new Float32Array(BONES.length * 2 * 3);
    const boneGeo = new THREE.BufferGeometry();
    boneGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const boneMat = new THREE.LineBasicMaterial({
        color, transparent: opacity < 1, opacity: Math.min(1, opacity + 0.1),
    });
    const bones = new THREE.LineSegments(boneGeo, boneMat);
    group.add(bones);
    return { group, joints, bones, positions };
}

// One view per hand side: its OWN scene (so a view can only ever show its
// hand), its own camera + interaction state, and a DOM corner label. The DOM
// label replaces the old in-scene L/R sprites: crisper text, and it no
// longer swims around while the user rotates the hand.
function makeView(side) {
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 10);
    const realRig = makeHandRig(COLOR_REAL, 1.0);
    const predRig = makeHandRig(COLOR_PRED, 0.55);
    realRig.group.visible = false;
    predRig.group.visible = false;
    scene.add(realRig.group, predRig.group);
    const labelEl = document.createElement('div');
    labelEl.textContent = side === 'left' ? 'L' : 'R';
    labelEl.style.cssText =
        'position:absolute;top:4px;font:bold 11px system-ui,sans-serif;' +
        'color:rgba(138,146,163,0.9);pointer-events:none;user-select:none;' +
        'display:none;z-index:1;';
    container.appendChild(labelEl);
    return {
        side, scene, camera, realRig, predRig, labelEl,
        outReal: Array.from({ length: N_JOINTS }, () => new THREE.Vector3()),
        outPred: Array.from({ length: N_JOINTS }, () => new THREE.Vector3()),
        active: false,                    // has live real-hand data
        rect: { x: 0, w: 0, h: 0 },       // CSS-pixel viewport inside canvas
        yaw: DEFAULT_YAW, pitch: DEFAULT_PITCH, zoom: 1,
        interacted: false,                // first touch kills idle drift
        focus: new THREE.Vector3(),       // framed hand centroid
        fitRadius: 0,                     // framed hand size
        needsReframe: true,               // force refit on next data update
    };
}

// Scratch objects reused per update (no per-frame allocation).
const _wristPos = new THREE.Vector3();
const _wristQuatInv = new THREE.Quaternion();
const _p = new THREE.Vector3();

// Transform a flat values array into wrist-local joint positions and write
// them into the rig. Returns false (and hides the rig) if the data is
// missing/degenerate. `outPositions` is an array of N_JOINTS Vector3 to fill.
function layoutHand(flat, rig, outPositions) {
    if (!flat || flat.length < N_JOINTS * FLOATS_PER_JOINT) {
        rig.group.visible = false;
        return false;
    }
    _wristPos.set(flat[0], flat[1], flat[2]);
    // Wrist quaternion -> inverse, with a degeneracy guard (a bad model can
    // emit a near-zero quat; inverting that yields NaN).
    const qx = flat[3], qy = flat[4], qz = flat[5], qw = flat[6];
    const qLenSq = qx * qx + qy * qy + qz * qz + qw * qw;
    let useRot = false;
    if (qLenSq > 1e-6) {
        _wristQuatInv.set(qx, qy, qz, qw).normalize().invert();
        useRot = true;
    }
    for (let i = 0; i < N_JOINTS; i++) {
        const b = i * FLOATS_PER_JOINT;
        _p.set(flat[b], flat[b + 1], flat[b + 2]).sub(_wristPos);
        if (useRot) _p.applyQuaternion(_wristQuatInv);
        rig.joints[i].position.copy(_p);
        outPositions[i].copy(_p);
    }
    // Update bone vertices from the laid-out joint positions.
    const pos = rig.positions;
    for (let k = 0; k < BONES.length; k++) {
        const [a, c] = BONES[k];
        const pa = outPositions[a], pc = outPositions[c];
        const o = k * 6;
        pos[o] = pa.x; pos[o + 1] = pa.y; pos[o + 2] = pa.z;
        pos[o + 3] = pc.x; pos[o + 4] = pc.y; pos[o + 5] = pc.z;
    }
    rig.bones.geometry.attributes.position.needsUpdate = true;
    rig.group.visible = true;
    return true;
}

// Robust framing, now PER HAND: centroid + 90th-percentile joint distance,
// so one garbage joint from a bad packet cannot blow the view up. Only
// applied on a MATERIAL size change (or an explicit reset): refitting every
// frame would fight the user's wheel zoom, which multiplies the fit distance.
const _centroid = new THREE.Vector3();
const _dists = new Array(N_JOINTS).fill(0);
const _asc = (a, b) => a - b;

function reframeIfNeeded(view) {
    _centroid.set(0, 0, 0);
    for (let i = 0; i < N_JOINTS; i++) _centroid.add(view.outReal[i]);
    _centroid.multiplyScalar(1 / N_JOINTS);
    for (let i = 0; i < N_JOINTS; i++) {
        _dists[i] = view.outReal[i].distanceTo(_centroid);
    }
    _dists.sort(_asc);
    const r = Math.max(_dists[Math.floor(N_JOINTS * 0.9)] * 1.25, 0.02);
    if (!view.needsReframe && view.fitRadius > 0 &&
        Math.abs(r - view.fitRadius) / view.fitRadius < REFRAME_FRAC) {
        return;
    }
    view.needsReframe = false;
    view.fitRadius = r;
    view.focus.copy(_centroid);
}

let _lastW = 0, _lastH = 0;

// Split the container between the visible hands: two sub-views side by side
// when both stream, one full-width view when only one does ("1 and two
// handed version too"). Runs when the active set changes or the container
// resizes, NOT per frame.
function layoutViews() {
    if (!container) return;
    const w = container.clientWidth || 320;
    const h = container.clientHeight || 240;
    let count = 0;
    for (let i = 0; i < VIEW_SIDES.length; i++) {
        if (views[VIEW_SIDES[i]].active) count++;
    }
    const gap = count === 2 ? 2 : 0;   // thin gutter so it reads as two windows
    let x = 0;
    for (let i = 0; i < VIEW_SIDES.length; i++) {
        const v = views[VIEW_SIDES[i]];
        v.labelEl.style.display = v.active ? '' : 'none';
        if (!v.active) continue;
        const vw = count === 2 ? Math.floor((w - gap) / 2) : w;
        v.rect.x = x; v.rect.w = vw; v.rect.h = h;
        v.camera.aspect = vw / h;
        v.camera.updateProjectionMatrix();
        v.labelEl.style.left = (x + 6) + 'px';
        x += vw + gap;
    }
}

function resize() {
    if (!container || !renderer) return;
    const w = container.clientWidth || 320;
    const h = container.clientHeight || 240;
    // Skip no-op resizes: setVisible(true) fires every tick, and setSize
    // clears the drawing buffer even when the size hasn't changed.
    if (w === _lastW && h === _lastH) return;
    _lastW = w; _lastH = h;
    renderer.setSize(w, h, false);
    layoutViews();
}

// Place a view's orbit camera from its yaw/pitch/zoom. The hand is
// wrist-local so it sits near the origin; orbit the framed centroid. Fit
// against the NARROWER field axis so a half-width view (two-hand split)
// does not clip fingertips.
function positionView(view) {
    const cam = view.camera;
    const halfV = THREE.MathUtils.degToRad(cam.fov) / 2;
    const half = Math.min(halfV, Math.atan(Math.tan(halfV) * cam.aspect));
    const r = Math.max(0.12, (view.fitRadius / Math.sin(half)) * 1.4) * view.zoom;
    cam.position.set(
        view.focus.x + r * Math.cos(view.pitch) * Math.sin(view.yaw),
        view.focus.y + r * Math.sin(view.pitch),
        view.focus.z + r * Math.cos(view.pitch) * Math.cos(view.yaw),
    );
    cam.lookAt(view.focus);
}

// Draw every active view through the one renderer with viewport + scissor.
// dt is seconds since the previous frame (0 for synchronous snapshot
// renders, which must not advance the drift).
function renderFrame(dt) {
    renderer.setScissorTest(false);
    renderer.clear();
    renderer.setScissorTest(true);
    for (let i = 0; i < VIEW_SIDES.length; i++) {
        const v = views[VIEW_SIDES[i]];
        if (!v.active) continue;
        // Idle drift: slow enough to just hint at depth, and ONLY until the
        // user touches this view (the always-on spin was the complaint).
        if (!v.interacted && dt > 0) v.yaw += IDLE_DRIFT * dt;
        positionView(v);
        renderer.setViewport(v.rect.x, 0, v.rect.w, v.rect.h);
        renderer.setScissor(v.rect.x, 0, v.rect.w, v.rect.h);
        renderer.render(v.scene, v.camera);
    }
    renderer.setScissorTest(false);
}

function animate(t) {
    raf = requestAnimationFrame(animate);
    // Skip ALL work while hidden; reset the clock so re-show does not apply
    // a huge accumulated drift step.
    if (!visible || !renderer) { lastT = 0; return; }
    const dt = lastT ? Math.min((t - lastT) / 1000, 0.1) : 0;
    lastT = t;
    renderFrame(dt);
}

// Hit-test a canvas-local x against the active view rects (views are full
// height, so x alone decides). Routes drag/wheel/dblclick to the view under
// the cursor ONLY: interacting with one hand must not move the other.
function viewAt(x) {
    for (let i = 0; i < VIEW_SIDES.length; i++) {
        const v = views[VIEW_SIDES[i]];
        if (v.active && x >= v.rect.x && x < v.rect.x + v.rect.w) return v;
    }
    return null;
}

const OMHandViewer = {
    init(containerEl) {
        if (renderer) return;   // idempotent
        container = containerEl;
        // The L/R corner labels are absolutely positioned INSIDE the
        // container; give it a positioning context without touching any
        // layout outside it.
        if (getComputedStyle(container).position === 'static') {
            container.style.position = 'relative';
        }
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        // The canvas must never drive layout: setSize(..., false) sets the
        // canvas width/height ATTRIBUTES (drawing buffer), and without CSS
        // sizing those become the layout size. The container then grows to
        // fit, resize() reads the bigger clientWidth, and the loop runs the
        // canvas up to the GPU's max texture size. CSS-pin it to the
        // container instead.
        renderer.domElement.style.width = '100%';
        renderer.domElement.style.height = '100%';
        container.appendChild(renderer.domElement);

        views.left = makeView('left');
        views.right = makeView('right');

        const el = renderer.domElement;
        el.style.cursor = 'grab';
        el.style.touchAction = 'none';   // pointer-drag, not touch scrolling

        // Drag rotates only the view under the cursor. Pointer capture keeps
        // the drag alive when the cursor leaves the view or the canvas.
        el.addEventListener('pointerdown', (e) => {
            const v = viewAt(e.offsetX);
            if (!v) return;
            v.interacted = true;         // any interaction kills idle drift
            dragView = v;
            lastX = e.clientX; lastY = e.clientY;
            el.style.cursor = 'grabbing';
            el.setPointerCapture(e.pointerId);
        });
        el.addEventListener('pointermove', (e) => {
            if (!dragView) return;
            dragView.yaw -= (e.clientX - lastX) * 0.01;
            dragView.pitch = Math.max(-1.4, Math.min(1.4,
                dragView.pitch + (e.clientY - lastY) * 0.01));
            lastX = e.clientX; lastY = e.clientY;
        });
        const endDrag = () => { dragView = null; el.style.cursor = 'grab'; };
        el.addEventListener('pointerup', endDrag);
        el.addEventListener('pointercancel', endDrag);

        // Wheel zoom per view, clamped around the fit distance. Registered
        // non-passive so preventDefault sticks: the canvas swallows the
        // scroll, the rest of the page keeps scrolling normally.
        el.addEventListener('wheel', (e) => {
            e.preventDefault();
            const v = viewAt(e.offsetX);
            if (!v) return;
            v.interacted = true;
            v.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX,
                v.zoom * Math.exp(e.deltaY * 0.0012)));
        }, { passive: false });

        // Double-click resets THAT view to the default framing: angles,
        // zoom, and a fresh fit on the next data update. It does not revive
        // the drift; a reset is still an interaction.
        el.addEventListener('dblclick', (e) => {
            const v = viewAt(e.offsetX);
            if (!v) return;
            v.interacted = true;
            v.yaw = DEFAULT_YAW; v.pitch = DEFAULT_PITCH; v.zoom = 1;
            v.needsReframe = true;
        });

        window.addEventListener('resize', resize);
        // The Live stage can resize the panel without a window resize
        // (sidebar toggles, splitters), so watch the container itself too.
        if (typeof ResizeObserver !== 'undefined') {
            new ResizeObserver(resize).observe(container);
        }
        resize();
        raf = requestAnimationFrame(animate);
    },

    // Per-side update. side: 'left' | 'right'. realFlat: live captured hand
    // (null/short hides that side and collapses its view). predFlat: model
    // prediction for that side's band (null/short hides the ghost).
    updateHand(side, realFlat, predFlat) {
        if (!renderer) return;
        const v = views[side];
        if (!v) return;
        const hasReal = layoutHand(realFlat, v.realRig, v.outReal);
        if (hasReal && predFlat && predFlat.length >= N_JOINTS * FLOATS_PER_JOINT) {
            layoutHand(predFlat, v.predRig, v.outPred);
        } else {
            v.predRig.group.visible = false;
        }
        if (hasReal) reframeIfNeeded(v);
        if (v.active !== hasReal) {
            // A hand appeared or went stale: re-split the container live
            // (one full-width view <-> two side-by-side views).
            v.active = hasReal;
            layoutViews();
        }
    },

    // Back-compat single-hand API (pre-two-slot callers): drives the right
    // view and clears the left, which collapses to one full-width view.
    update(realFlat, predFlat) {
        if (!renderer) return;
        this.updateHand('left', null, null);
        this.updateHand('right', realFlat, predFlat);
    },

    setVisible(v) {
        visible = !!v;
        if (container) container.style.display = v ? '' : 'none';
        if (v) resize();
    },

    // Render one frame synchronously and return it as a PNG data URL. The
    // synchronous render + toDataURL captures the drawing buffer before the
    // compositor clears it, so it works without preserveDrawingBuffer. All
    // sub-views share the one canvas, so both hands land in the same PNG
    // with no compositing step. Handy for frame thumbnails and for headless
    // verification of the render.
    snapshot() {
        if (!renderer) return null;
        renderFrame(0);
        return renderer.domElement.toDataURL('image/png');
    },

    isReady() { return !!renderer; },
};

window.OMHandViewer = OMHandViewer;

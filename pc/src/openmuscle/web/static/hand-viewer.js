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
//     OMHandViewer.update(realFlat, predFlat)   // flat [px,py,pz,rx,ry,rz,rw]*N
//     OMHandViewer.setVisible(bool)
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

let scene, camera, renderer, container;
let raf = null;
let visible = false;
let autoRotate = true;
let yaw = 0.6, pitch = -0.25;   // view angles (radians)
let dragging = false, lastX = 0, lastY = 0;

// One reusable hand rig = 25 joint spheres + bone line-segments + a label.
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

// TWO-HAND SLOTS (Tory, Clark session: "I only see one virtual hand"): the
// viewer used to hold a single real+pred rig pair, so two-hand mode (quest-left
// + quest-right) only ever showed the first device. Each side now has its own
// slot -- real + predicted rigs inside a parent group offset left/right of the
// origin, with an L/R sprite so the sides stay identifiable while orbiting.
const SLOT_OFFSET_X = 0.11;
const slots = { left: null, right: null };

function makeSideLabel(text) {
    const c = document.createElement('canvas');
    c.width = c.height = 128;
    const ctx = c.getContext('2d');
    ctx.fillStyle = 'rgba(138, 146, 163, 0.9)';
    ctx.font = 'bold 96px sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(text, 64, 70);
    const tex = new THREE.CanvasTexture(c);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
        map: tex, transparent: true, depthTest: false }));
    sprite.scale.set(0.04, 0.04, 1);
    sprite.position.set(0, -0.11, 0);
    return sprite;
}

function makeSlot(side) {
    const parent = new THREE.Group();
    parent.position.x = side === 'left' ? -SLOT_OFFSET_X : SLOT_OFFSET_X;
    const realRig = makeHandRig(COLOR_REAL, 1.0);
    const predRig = makeHandRig(COLOR_PRED, 0.55);
    predRig.group.visible = false;
    const label = makeSideLabel(side === 'left' ? 'L' : 'R');
    label.visible = false;
    parent.add(realRig.group, predRig.group, label);
    scene.add(parent);
    return {
        parent, realRig, predRig, label,
        outReal: Array.from({ length: N_JOINTS }, () => new THREE.Vector3()),
        outPred: Array.from({ length: N_JOINTS }, () => new THREE.Vector3()),
    };
}

// Scratch objects reused per update (no per-frame allocation).
const _wristPos = new THREE.Vector3();
const _wristQuatInv = new THREE.Quaternion();
const _p = new THREE.Vector3();

// Transform a flat values array into wrist-local joint positions and write
// them into the rig. Returns false (and hides the rig) if the data is
// missing/degenerate. `out` is an array of N_JOINTS THREE.Vector3 to fill.
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

// Auto-framing target + fit radius, recomputed over EVERY visible hand (plus
// its slot offset) so one hand fills the view and two hands both fit.
const _focus = new THREE.Vector3();
let _fitRadius = 0.1;
const _fp = new THREE.Vector3();

function recomputeFraming() {
    _focus.set(0, 0, 0);
    let count = 0;
    for (const side of ['left', 'right']) {
        const s = slots[side];
        if (!s || !s.realRig.group.visible) continue;
        for (let i = 0; i < N_JOINTS; i++) {
            _focus.add(_fp.copy(s.outReal[i]).add(s.parent.position));
            count++;
        }
    }
    if (!count) return;
    _focus.multiplyScalar(1 / count);
    let maxR = 0;
    for (const side of ['left', 'right']) {
        const s = slots[side];
        if (!s || !s.realRig.group.visible) continue;
        for (let i = 0; i < N_JOINTS; i++) {
            const d = _fp.copy(s.outReal[i]).add(s.parent.position).distanceTo(_focus);
            if (d > maxR) maxR = d;
        }
    }
    _fitRadius = maxR || 0.1;
}

let _lastW = 0, _lastH = 0;

function resize() {
    if (!container || !renderer) return;
    const w = container.clientWidth || 320;
    const h = container.clientHeight || 240;
    // Skip no-op resizes: setVisible(true) fires every tick, and setSize
    // clears the drawing buffer even when the size hasn't changed.
    if (w === _lastW && h === _lastH) return;
    _lastW = w; _lastH = h;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
}

// Place the orbit camera from the current yaw/pitch. Hands are wrist-centered
// at the origin, so the camera always looks at (0,0,0). Shared by the animate
// loop and snapshot() so a snapshot never renders from an unpositioned camera.
function positionCamera() {
    // Distance to fit a sphere of _fitRadius given the camera's vertical FOV,
    // with margin. Orbit around the hand centroid (_focus), not the wrist.
    const half = THREE.MathUtils.degToRad(camera.fov) / 2;
    const r = Math.max(0.12, (_fitRadius / Math.sin(half)) * 1.4);
    camera.position.set(
        _focus.x + r * Math.cos(pitch) * Math.sin(yaw),
        _focus.y + r * Math.sin(pitch),
        _focus.z + r * Math.cos(pitch) * Math.cos(yaw),
    );
    camera.lookAt(_focus);
}

function animate() {
    raf = requestAnimationFrame(animate);
    if (!visible) return;
    if (autoRotate && !dragging) yaw += 0.005;
    positionCamera();
    renderer.render(scene, camera);
}

const OMHandViewer = {
    init(containerEl) {
        if (renderer) return;   // idempotent
        container = containerEl;
        scene = new THREE.Scene();
        camera = new THREE.PerspectiveCamera(45, 1, 0.01, 10);
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

        slots.left = makeSlot('left');
        slots.right = makeSlot('right');

        // Drag to rotate (pauses auto-rotate while dragging).
        const el = renderer.domElement;
        el.style.cursor = 'grab';
        el.addEventListener('pointerdown', (e) => {
            dragging = true; lastX = e.clientX; lastY = e.clientY;
            el.style.cursor = 'grabbing'; el.setPointerCapture(e.pointerId);
        });
        el.addEventListener('pointermove', (e) => {
            if (!dragging) return;
            yaw -= (e.clientX - lastX) * 0.01;
            pitch = Math.max(-1.4, Math.min(1.4, pitch + (e.clientY - lastY) * 0.01));
            lastX = e.clientX; lastY = e.clientY;
        });
        const endDrag = () => { dragging = false; el.style.cursor = 'grab'; };
        el.addEventListener('pointerup', endDrag);
        el.addEventListener('pointercancel', endDrag);
        // Double-click resets to auto-rotate.
        el.addEventListener('dblclick', () => { autoRotate = true; });

        window.addEventListener('resize', resize);
        resize();
        animate();
    },

    // Per-side update. side: 'left' | 'right'. realFlat: live captured hand
    // (null/short hides that side). predFlat: model prediction for that side's
    // band (null/short hides the ghost).
    updateHand(side, realFlat, predFlat) {
        if (!renderer) return;
        const s = slots[side];
        if (!s) return;
        const hasReal = layoutHand(realFlat, s.realRig, s.outReal);
        s.label.visible = hasReal;
        if (hasReal && predFlat && predFlat.length >= N_JOINTS * FLOATS_PER_JOINT) {
            layoutHand(predFlat, s.predRig, s.outPred);
        } else {
            s.predRig.group.visible = false;
        }
        recomputeFraming();
    },

    // Back-compat single-hand API (pre-two-slot callers): drives the right
    // slot and clears the left.
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
    // compositor clears it, so it works without preserveDrawingBuffer. Handy
    // for frame thumbnails and for headless verification of the render.
    snapshot() {
        if (!renderer) return null;
        positionCamera();
        renderer.render(scene, camera);
        return renderer.domElement.toDataURL('image/png');
    },

    isReady() { return !!renderer; },
};

window.OMHandViewer = OMHandViewer;

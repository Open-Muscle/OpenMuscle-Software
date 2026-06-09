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

let realRig, predRig;

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

const _realOut = Array.from({ length: N_JOINTS }, () => new THREE.Vector3());
const _predOut = Array.from({ length: N_JOINTS }, () => new THREE.Vector3());

function resize() {
    if (!container || !renderer) return;
    const w = container.clientWidth || 320;
    const h = container.clientHeight || 240;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
}

function animate() {
    raf = requestAnimationFrame(animate);
    if (!visible) return;
    if (autoRotate && !dragging) yaw += 0.005;
    // Orbit camera around the origin (hands are wrist-centered at origin).
    const r = 0.34;
    camera.position.set(
        r * Math.cos(pitch) * Math.sin(yaw),
        r * Math.sin(pitch),
        r * Math.cos(pitch) * Math.cos(yaw),
    );
    camera.lookAt(0, 0, 0);
    renderer.render(scene, camera);
}

const OMHandViewer = {
    init(containerEl) {
        if (renderer) return;   // idempotent
        container = containerEl;
        scene = new THREE.Scene();
        camera = new THREE.PerspectiveCamera(45, 1, 0.01, 10);
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(window.devicePixelRatio || 1);
        container.appendChild(renderer.domElement);

        realRig = makeHandRig(COLOR_REAL, 1.0);
        predRig = makeHandRig(COLOR_PRED, 0.55);
        predRig.group.visible = false;
        scene.add(realRig.group);
        scene.add(predRig.group);

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

    // realFlat: live captured hand. predFlat: model prediction (or null/short
    // for a non-quest model -> predicted hand hidden).
    update(realFlat, predFlat) {
        if (!renderer) return;
        layoutHand(realFlat, realRig, _realOut);
        if (predFlat && predFlat.length >= N_JOINTS * FLOATS_PER_JOINT) {
            layoutHand(predFlat, predRig, _predOut);
        } else {
            predRig.group.visible = false;
        }
    },

    setVisible(v) {
        visible = !!v;
        if (container) container.style.display = v ? '' : 'none';
        if (v) resize();
    },

    isReady() { return !!renderer; },
};

window.OMHandViewer = OMHandViewer;

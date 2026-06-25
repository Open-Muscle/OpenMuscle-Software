// OpenMuscle desktop IMU orientation widget.
//
// Renders the FlexGrid band's live pose from the fast data.imu = {gyro[3],
// accel[3]} stream (PROTOCOL.md 7.1, ~18-20Hz). A small 3D band model tilts to
// match the physical band, plus a gravity-vector arrow. Loaded as an ES module
// from index.html; exposes window.OMImuViewer for the plain app.js to drive:
//     OMImuViewer.init(containerEl)
//     OMImuViewer.update({gyro:[...], accel:[...]})
//     OMImuViewer.setVisible(bool)
//
// v1 ORIENTATION = ACCEL-TILT (scale-independent): the normalized accel vector
// is gravity-up in the band's local frame, so we orient the model's normal to
// follow it. This gives correct pitch/roll from accel alone with NO gyro-scale
// dependency. Yaw has no magnetometer reference, so it is not driven here (the
// minimal setFromUnitVectors rotation leaves yaw stable). Gyro is shown in the
// readout and reserved for future smoothing / yaw integration once the
// counts->rad/s scale is pinned with firmware.
//
// AXIS MAPPING (ACCEL_TO_MODEL_UP) is the one convention to align with the phone
// + VR hubs so all three show the band identically (board #0197). It is isolated
// as a single function below; only that needs to change to match phone.

import * as THREE from 'three';

const COLOR_BAND = 0x3b82f6;     // blue band
const COLOR_EDGE = 0xfbbf24;     // amber "top edge" marker (shows roll/yaw)
const COLOR_GRAV = 0x34d399;     // emerald gravity arrow

// Map the raw sensor accel [ax,ay,az] to a Three.js "up" vector in MODEL space.
// At rest the band reads accel ~[-440, 0, 2150] (gravity along sensor +Z), and we
// want the band to appear FLAT (model up = +Y). So sensor +Z -> model +Y,
// sensor +X -> model +X, sensor +Y -> model -Z. TUNABLE: flip axes/signs here to
// match the phone widget (board #0197).
function accelToModelUp(ax, ay, az, out) {
    return out.set(ax, az, -ay);
}

let scene, camera, renderer, container;
let band, edge, gravArrow;
let raf = null;
let visible = false;
let yaw = 0.7, pitch = -0.35;            // camera orbit angles
let dragging = false, lastX = 0, lastY = 0;

// Scratch (no per-frame allocation).
const _up = new THREE.Vector3();
const _modelNormal = new THREE.Vector3(0, 1, 0);  // band's flat normal
const _targetQuat = new THREE.Quaternion();

let _lastW = 0, _lastH = 0;

function resize() {
    if (!container || !renderer) return;
    const w = container.clientWidth || 280;
    const h = container.clientHeight || 200;
    if (w === _lastW && h === _lastH) return;
    _lastW = w; _lastH = h;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
}

function animate() {
    raf = requestAnimationFrame(animate);
    if (!visible) return;
    // Smooth the band toward the target orientation (low-pass for a steady feel
    // at ~18-20Hz; slerp factor is frame-rate-tolerant enough for a viz).
    band.quaternion.slerp(_targetQuat, 0.25);
    // edge is a child of band, so it inherits the orientation automatically.
    // Orbit camera around origin.
    const r = 0.32;
    camera.position.set(
        r * Math.cos(pitch) * Math.sin(yaw),
        r * Math.sin(pitch),
        r * Math.cos(pitch) * Math.cos(yaw),
    );
    camera.lookAt(0, 0, 0);
    renderer.render(scene, camera);
}

const OMImuViewer = {
    init(containerEl) {
        if (renderer) return;   // idempotent
        container = containerEl;
        scene = new THREE.Scene();
        camera = new THREE.PerspectiveCamera(45, 1, 0.01, 10);
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(window.devicePixelRatio || 1);
        container.appendChild(renderer.domElement);

        // Band: a thin flat box, 15:4 aspect (like the FlexGrid grid).
        band = new THREE.Mesh(
            new THREE.BoxGeometry(0.15, 0.006, 0.04),
            new THREE.MeshBasicMaterial({ color: COLOR_BAND, transparent: true, opacity: 0.85 }),
        );
        // Wireframe outline so tilt reads clearly.
        band.add(new THREE.LineSegments(
            new THREE.EdgesGeometry(band.geometry),
            new THREE.LineBasicMaterial({ color: 0x93c5fd }),
        ));
        scene.add(band);

        // Amber marker bar on the +X end so roll/yaw are visible (not just tilt).
        // Child of the band so it inherits the band's orientation automatically.
        edge = new THREE.Mesh(
            new THREE.BoxGeometry(0.008, 0.009, 0.044),
            new THREE.MeshBasicMaterial({ color: COLOR_EDGE }),
        );
        edge.position.set(0.075, 0, 0);
        band.add(edge);

        // Fixed world gravity arrow (points down) for reference.
        gravArrow = new THREE.ArrowHelper(
            new THREE.Vector3(0, -1, 0), new THREE.Vector3(0, 0.08, 0),
            0.06, COLOR_GRAV, 0.018, 0.012);
        scene.add(gravArrow);

        // Drag to orbit.
        const el = renderer.domElement;
        el.style.cursor = 'grab'; el.style.touchAction = 'none';
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
        const end = () => { dragging = false; el.style.cursor = 'grab'; };
        el.addEventListener('pointerup', end);
        el.addEventListener('pointercancel', end);

        window.addEventListener('resize', resize);
        resize();
        animate();
    },

    // imu = {gyro:[gx,gy,gz], accel:[ax,ay,az]} (raw counts).
    update(imu) {
        if (!renderer || !imu) return;
        const a = imu.accel;
        if (!Array.isArray(a) || a.length < 3) return;
        // Degenerate (near-zero accel = freefall or bad read): keep last pose.
        const mag = Math.hypot(a[0], a[1], a[2]);
        if (mag < 1e-3) return;
        accelToModelUp(a[0], a[1], a[2], _up).normalize();
        _targetQuat.setFromUnitVectors(_modelNormal, _up);
    },

    setVisible(v) {
        // Controls only the render loop. The caller owns the panel wrapper's
        // display (the canvas mount lives inside that wrapper).
        visible = !!v;
        if (v) resize();
    },

    isReady() { return !!renderer; },

    // Inspection hook (tests/verification): the current target orientation as a
    // quaternion array, so a caller can confirm the band reorients to new accel.
    _debugTarget() { return _targetQuat.toArray(); },
};

window.OMImuViewer = OMImuViewer;

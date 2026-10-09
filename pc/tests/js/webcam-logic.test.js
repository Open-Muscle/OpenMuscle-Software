// Node unit tests for static/webcam/track-logic.js: hand selection, the tracker
// failure policy and the plain camera / tracker error wording.
// Run: node --test pc/tests/js/webcam-logic.test.js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SRC = path.join(__dirname, '..', '..', 'src', 'openmuscle', 'web', 'static', 'webcam', 'track-logic.js');

// track-logic.js is an ES module with no imports; load it through a data: URL
// so this works whatever Node decides about a bare .js file.
let modPromise;
function load() {
    if (!modPromise) {
        const src = fs.readFileSync(SRC, 'utf8');
        modPromise = import('data:text/javascript;base64,' + Buffer.from(src).toString('base64'));
    }
    return modPromise;
}

// One tracker frame: hands = [{label, score, x}] where x places the wrist.
function frame(hands) {
    return hands.map((h, i) => ({
        index: i, label: h.label, score: h.score ?? 0.9, wrist: [h.x ?? 0.5, h.y ?? 0.5],
    }));
}

// Feed frames at 30 Hz; returns one sends[] per frame.
function replay(sel, frames, startMs = 0) {
    return frames.map((hands, i) => sel.update(frame(hands), startMs + i * 33).sends);
}

const steady = (n, hand) => Array.from({ length: n }, () => [hand]);

test('actualSide: the tracker sees a mirror image, swap flips it', async () => {
    const { actualSide } = await load();
    assert.equal(actualSide('Left'), 'right');
    assert.equal(actualSide('Right'), 'left');
    assert.equal(actualSide('Left', true), 'left');
    assert.equal(actualSide('Right', true), 'right');
});

test('one flipped frame creates no second device and no wrong label', async () => {
    const { HandSelector } = await load();
    const sel = new HandSelector();
    const frames = steady(60, { label: 'Left' });       // the person's right hand
    frames[30] = [{ label: 'Right' }];                  // tracker flips for one frame
    frames[31] = [{ label: 'Right' }];                  // ...even for two
    const out = replay(sel, frames).flat();
    const sides = new Set(out.map(s => s.side));
    assert.deepEqual([...sides], ['right']);
    // Once locked on (3 frames) it sends every frame, including the flipped ones.
    assert.equal(out.length, 60 - 2);
});

test('a flipped very first frame is outvoted before anything is sent', async () => {
    const { HandSelector } = await load();
    const sel = new HandSelector();
    const frames = steady(20, { label: 'Left' });
    frames[0] = [{ label: 'Right' }];
    const out = replay(sel, frames);
    assert.deepEqual(out[0], []);
    assert.deepEqual(out[1], []);
    const sides = new Set(out.flat().map(s => s.side));
    assert.deepEqual([...sides], ['right']);
});

test('a real, sustained change of side does switch the label', async () => {
    const { HandSelector } = await load();
    const sel = new HandSelector();
    const out = replay(sel, [...steady(15, { label: 'Left' }), ...steady(30, { label: 'Right' })]);
    assert.equal(out[14][0].side, 'right');
    assert.equal(out[44][0].side, 'left');
});

test('a second hand does not hijack the side that is already being sent', async () => {
    const { HandSelector } = await load();
    const sel = new HandSelector();
    const first = steady(20, { label: 'Left', x: 0.3, score: 0.7 });
    // A second hand appears, also called "Left" by the tracker and with a better score.
    const both = steady(30, null).map(() => [
        { label: 'Left', x: 0.3, score: 0.7 },
        { label: 'Left', x: 0.75, score: 0.99 },
    ]);
    const out = replay(sel, [...first, ...both]);
    const during = out.slice(25).flat();
    assert.ok(during.length > 0);
    for (const s of during) {
        assert.equal(s.side, 'right');
        assert.equal(s.index, 0);       // still the original hand
    }
    assert.equal(new Set(during.map(s => s.trackId)).size, 1);
});

test('two hands, one per side, are both sent', async () => {
    const { HandSelector } = await load();
    const sel = new HandSelector();
    const frames = steady(15, null).map(() => [
        { label: 'Left', x: 0.25 },     // the person's right hand
        { label: 'Right', x: 0.75 },    // the person's left hand
    ]);
    const out = replay(sel, frames);
    const last = out[14];
    assert.deepEqual(last.map(s => s.side).sort(), ['left', 'right']);
    assert.equal(last.find(s => s.side === 'right').index, 0);
    assert.equal(last.find(s => s.side === 'left').index, 1);
});

test('detections may come back in a different order each frame', async () => {
    const { HandSelector } = await load();
    const sel = new HandSelector();
    const a = { label: 'Left', x: 0.25 };
    const b = { label: 'Right', x: 0.75 };
    const frames = [];
    for (let i = 0; i < 30; i++) frames.push(i % 2 ? [a, b] : [b, a]);
    const out = replay(sel, frames);
    for (const sends of out.slice(5)) {
        const right = sends.find(s => s.side === 'right');
        const left = sends.find(s => s.side === 'left');
        assert.ok(right && left);
    }
});

test('swap left and right flips the result and starts the votes over', async () => {
    const { HandSelector } = await load();
    const sel = new HandSelector();
    replay(sel, steady(20, { label: 'Left' }));
    sel.setSwap(true);
    const out = replay(sel, steady(10, { label: 'Left' }), 1000);
    assert.equal(out[9][0].side, 'left');
});

test('explicit hand choice: exactly one hand, called what the operator said', async () => {
    const { HandSelector } = await load();
    const sel = new HandSelector({ mode: 'left' });
    const frames = steady(20, null).map(() => [
        { label: 'Left', x: 0.3, score: 0.8 },     // tracker says right-hand
        { label: 'Left', x: 0.75, score: 0.95 },
    ]);
    const out = replay(sel, frames);
    const last = out[19];
    assert.equal(last.length, 1);
    assert.equal(last[0].side, 'left');
    // It keeps the same hand even though the other has the better score.
    const ids = new Set(out.slice(5).flat().map(s => s.trackId));
    assert.equal(ids.size, 1);
});

test('a hand that moves across the picture keeps its identity', async () => {
    const { HandSelector } = await load();
    const sel = new HandSelector();
    const frames = Array.from({ length: 40 }, (_, i) => [{ label: 'Left', x: 0.2 + i * 0.015 }]);
    const out = replay(sel, frames).flat();
    assert.equal(new Set(out.map(s => s.trackId)).size, 1);
});

test('a hand that left for a while is forgotten and a new one is adopted', async () => {
    const { HandSelector } = await load();
    const sel = new HandSelector();
    const out1 = replay(sel, steady(15, { label: 'Left', x: 0.3 }));
    const id1 = out1[14][0].trackId;
    replay(sel, steady(30, null).map(() => []), 600);     // nothing in view for ~1 s
    const out2 = replay(sel, steady(15, { label: 'Left', x: 0.8 }), 2000);
    assert.notEqual(out2[14][0].trackId, id1);
    assert.equal(out2[14][0].side, 'right');
});

test('no hands in view sends nothing', async () => {
    const { HandSelector } = await load();
    const sel = new HandSelector();
    assert.deepEqual(sel.update([], 0).sends, []);
});

test('failure policy: GPU error -> CPU once; two CPU errors in a row -> stop', async () => {
    const { newFailureState, onDetectError, onDetectOk } = await load();
    const st = newFailureState('GPU');
    assert.equal(onDetectError(st), 'fallback-cpu');
    assert.equal(st.delegate, 'CPU');
    assert.equal(onDetectError(st), 'retry');
    onDetectOk(st);                                 // a good frame resets the count
    assert.equal(onDetectError(st), 'retry');
    assert.equal(onDetectError(st), 'stop');
});

test('failure policy: a tracker that started on CPU never "falls back"', async () => {
    const { newFailureState, onDetectError } = await load();
    const st = newFailureState('CPU');
    assert.equal(onDetectError(st), 'retry');
    assert.equal(onDetectError(st), 'stop');
});

const err = (name, message = '') => Object.assign(new Error(message), { name });

test('camera errors: each case has its own plain text', async () => {
    const { describeError } = await load();
    const d = (name, os, extra) => describeError(err(name), Object.assign({ stage: 'camera', os }, extra));
    assert.equal(d('NotAllowedError', 'windows').kind, 'permission');
    assert.equal(d('NotReadableError', 'windows').kind, 'busy');
    assert.equal(d('NotFoundError', 'windows').kind, 'nocamera');
    assert.equal(d('OverconstrainedError', 'windows').kind, 'constraint');
    assert.equal(d('SecurityError', 'windows', { location: { port: '8000' } }).kind, 'insecure');
    assert.equal(d('SomethingElse', 'windows').kind, 'unknown');
});

test('camera errors: wording is specific to the operating system', async () => {
    const { describeError } = await load();
    const text = (name, os) => describeError(err(name), { stage: 'camera', os }).text;
    for (const name of ['NotAllowedError', 'NotReadableError', 'NotFoundError']) {
        const w = text(name, 'windows');
        const m = text(name, 'mac');
        const l = text(name, 'linux');
        assert.equal(new Set([w, m, l]).size, 3, name);
    }
    assert.match(text('NotAllowedError', 'windows'), /Windows Settings > Privacy & security > Camera/);
    assert.match(text('NotAllowedError', 'mac'), /System Settings > Privacy & Security > Camera/);
    assert.match(text('NotReadableError', 'mac'), /FaceTime/);
    assert.match(text('NotFoundError', 'linux'), /ls \/dev\/video/);
});

test('camera errors: every message tells the person what to do next', async () => {
    const { describeError } = await load();
    for (const name of ['NotAllowedError', 'NotReadableError', 'NotFoundError',
                        'OverconstrainedError', 'SecurityError', 'Weird']) {
        for (const os of ['windows', 'mac', 'linux', 'other']) {
            const t = describeError(err(name, 'raw'), { stage: 'camera', os }).text;
            assert.ok(/Start camera|Open the camera page|Chrome/.test(t), `${name}/${os}: ${t}`);
            assert.ok(!/\bat \w+Error\b/.test(t));
        }
    }
});

test('insecure address: the hint is the real localhost URL for this port', async () => {
    const { describeError, cameraPageUrl } = await load();
    assert.equal(cameraPageUrl({ protocol: 'http:', port: '8004' }), 'http://localhost:8004/webcam');
    assert.equal(cameraPageUrl({ protocol: 'https:', port: '8443' }), 'https://localhost:8443/webcam');
    assert.equal(cameraPageUrl({ protocol: 'http:', port: '' }), 'http://localhost/webcam');
    const t = describeError(err('SecurityError'), {
        stage: 'camera', os: 'windows', location: { protocol: 'http:', port: '8004' } }).text;
    assert.match(t, /http:\/\/localhost:8004\/webcam/);
    assert.doesNotMatch(t, /8000/);
});

test('tracker errors: offline download is named as such', async () => {
    const { describeError } = await load();
    const msgs = ['Failed to fetch dynamically imported module: https://cdn.example/x.mjs',
                  'error loading dynamically imported module',
                  'Importing a module script failed.',
                  'NetworkError when attempting to fetch resource.'];
    for (const m of msgs) {
        const d = describeError(new TypeError(m), { stage: 'tracker', os: 'windows', online: true });
        assert.equal(d.kind, 'offline-tracker', m);
        assert.match(d.text, /Could not download the hand tracker, check internet access/);
        assert.match(d.text, /openmuscle assets fetch/);
    }
    const off = describeError(err('Error', 'anything'), { stage: 'tracker', online: false });
    assert.equal(off.kind, 'offline-tracker');
});

test('tracker errors: a non-network failure is reported as a start problem', async () => {
    const { describeError } = await load();
    const d = describeError(err('Error', 'WebGL context lost'), { stage: 'tracker', online: true });
    assert.equal(d.kind, 'tracker-init');
    assert.match(d.text, /WebGL context lost/);
});

test('loop failure message mentions the CPU mode only when it was tried', async () => {
    const { describeError } = await load();
    const a = describeError(err('Error', 'boom'), { stage: 'run', delegate: 'CPU' });
    const b = describeError(err('Error', 'boom'), { stage: 'run', delegate: 'GPU' });
    assert.match(a.text, /CPU/);
    assert.doesNotMatch(b.text, /CPU/);
    assert.match(a.text, /Start camera/);
});

test('detectOS', async () => {
    const { detectOS } = await load();
    assert.equal(detectOS('Win32', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'), 'windows');
    assert.equal(detectOS('MacIntel', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'), 'mac');
    assert.equal(detectOS('Linux x86_64', 'Mozilla/5.0 (X11; Linux x86_64)'), 'linux');
    assert.equal(detectOS('', ''), 'other');
});

test('the module has no em dashes (published text)', () => {
    assert.ok(!fs.readFileSync(SRC, 'utf8').includes(String.fromCharCode(0x2014)));
});

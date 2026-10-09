// Node unit tests for static/guide.js (record plan wording + label default).
// Run: node --test pc/tests/js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const G = require(path.join(__dirname, '..', '..', 'src', 'openmuscle', 'web', 'static', 'guide.js'));

const band = (id, role, age) => ({ device_id: id, device_type: 'flexgrid', role: role || null, last_seen_age: age ?? 0.1 });
const lask = (age) => ({ device_id: 'lask5-1', device_type: 'lask5', last_seen_age: age ?? 0.1 });
const quest = (id, age) => ({ device_id: id || 'quest-left', device_type: 'quest_hand', last_seen_age: age ?? 0.1 });
const cam = (age) => ({ device_id: 'webcam-left', device_type: 'webcam_hand', last_seen_age: age ?? 0.1 });
const pad = (age) => ({ device_id: 'gamepad-0', device_type: 'gamepad', last_seen_age: age ?? 0.1 });

const plan = (devices, source, extra) =>
    G.deriveRecordPlan(Object.assign({ devices, source, windowMs: null, sensorId: '', labelId: '' }, extra));

// ---- blocked reasons: exact strings ----
const BLOCKED = [
    ['no devices', [], 'lask5',
        'No band found yet. Turn your band on and wait about 10 seconds. See the Getting started panel if it does not appear.'],
    ['no devices, vr', [], 'vr',
        'No band found yet. Turn your band on and wait about 10 seconds. See the Getting started panel if it does not appear.'],
    ['only a labeler, no band', [lask()], 'lask5',
        'No band found yet. Turn your band on and wait about 10 seconds. See the Getting started panel if it does not appear.'],
    ['two untagged bands', [band('flexgrid-a'), band('flexgrid-b'), lask()], 'lask5',
        'Tell me which arm each band is on. Use the Left arm / Right arm buttons on each band.'],
    ['both tagged left', [band('flexgrid-a', 'left'), band('flexgrid-b', 'left'), lask()], 'lask5',
        'Both bands are set to the same arm (left). Set one of them to the right arm.'],
    ['both tagged right', [band('flexgrid-a', 'right'), band('flexgrid-b', 'right'), lask()], 'none',
        'Both bands are set to the same arm (right). Set one of them to the left arm.'],
    ['one tagged, one untagged', [band('flexgrid-a', 'left'), band('flexgrid-d7af0b'), lask()], 'lask5',
        'flexgrid-d7af0b has no arm set. Set it to Left arm or Right arm, or unplug it if you do not want to record it.'],
    ['one tagged, one untagged (vr)', [band('flexgrid-a', 'right'), band('flexgrid-x'), quest()], 'vr',
        'flexgrid-x has no arm set. Set it to Left arm or Right arm, or unplug it if you do not want to record it.'],
    ['lask5 not streaming', [band('flexgrid-a', 'left')], 'lask5',
        'The finger labeler (LASK5) is not sending data. Check it is turned on and on the same Wi-Fi as this computer.'],
    ['lask5 stale', [band('flexgrid-a', 'left'), lask(5)], 'lask5',
        'The finger labeler (LASK5) is not sending data. Check it is turned on and on the same Wi-Fi as this computer.'],
    ['vr not streaming', [band('flexgrid-a', 'left')], 'vr',
        'The headset is not sending hand data. Put on the Quest and open the OpenMuscle page, or change "Finger data from" to another labeler.'],
    ['vr both hands, one missing', [band('flexgrid-a', 'left'), band('flexgrid-b', 'right'), quest('quest-left')], 'vr',
        'Both of your hands need to be visible to the headset. Open the OpenMuscle page on the Quest with both hands selected.'],
    ['webcam not streaming', [band('flexgrid-a', 'left')], 'webcam',
        'The camera is not on. Open the camera page in a new tab (/webcam), press Start camera and hold your hand up.'],
    ['gamepad not streaming', [band('flexgrid-a', 'left')], 'gamepad',
        'The game controller is not sending data. Plug it in and press any button.'],
    ['stale band, lask5', [band('flexgrid-a', 'left', 6), lask()], 'lask5',
        'A band has stopped sending data. Check it is on and charged.'],
    ['stale band, none', [band('flexgrid-a', 'left', 6)], 'none',
        'A band has stopped sending data. Check it is on and charged.'],
    ['stale band, vr', [band('flexgrid-a', 'left', 6), quest()], 'vr',
        'A band has stopped sending data. Check it is on and charged.'],
    ['stale band, webcam', [band('flexgrid-a', 'left', 6), cam()], 'webcam',
        'A band has stopped sending data. Check it is on and charged.'],
    ['stale band, gamepad', [band('flexgrid-a', 'left', 6), pad()], 'gamepad',
        'A band has stopped sending data. Check it is on and charged.'],
    ['one of two tagged bands stale', [band('flexgrid-a', 'left'), band('flexgrid-b', 'right', 6), lask()], 'lask5',
        'A band has stopped sending data. Check it is on and charged.'],
    ['lone untagged band went stale', [band('flexgrid-a', null, 6), lask()], 'lask5',
        'A band has stopped sending data. Check it is on and charged.'],
];

for (const [name, devices, source, reason] of BLOCKED) {
    test(`blocked: ${name}`, () => {
        const p = plan(devices, source);
        assert.equal(p.ready, false);
        assert.equal(p.mode, 'blocked');
        assert.equal(p.reason, reason);
        assert.equal(p.text, reason);
    });
}

test('no blocked reason mentions the old Sources panel wording', () => {
    for (const [, devices, source] of BLOCKED) {
        const r = plan(devices, source).reason;
        assert.doesNotMatch(r, /Sources|tag roles|\/vr/);
    }
    for (const v of Object.values(G.REASONS)) {
        const s = typeof v === 'function' ? v('x', 'y') : v;
        assert.doesNotMatch(s, /Sources|tag roles/);
    }
});

// ---- ready paths ----
test('two tagged bands + LASK5 is ready with a plain sentence', () => {
    const p = plan([band('flexgrid-b', 'right'), band('flexgrid-a', 'left'), lask()], 'lask5');
    assert.equal(p.ready, true);
    assert.equal(p.mode, '2band-lask');
    assert.equal(p.endpoint, '/api/recording/multiband');
    assert.equal(p.text, 'Ready: 2 bands (left arm, right arm) + finger labeler. Press Record.');
    assert.match(p.summary, /^2 bands \| labels: LASK5 lask5-1 \| win 100ms$/);
});

test('lone untagged band + LASK5 is still allowed (server auto-picks)', () => {
    const p = plan([band('flexgrid-a'), lask()], 'lask5');
    assert.equal(p.ready, true);
    assert.equal(p.mode, '1h-lask');
    assert.deepEqual(p.body, { filename: null });
    assert.equal(p.text, 'Ready: 1 band + finger labeler. Press Record.');
});

test('lone tagged band + LASK5 sends sensor and role', () => {
    const p = plan([band('flexgrid-a', 'right'), lask()], 'lask5');
    assert.deepEqual(p.body, { filename: null, sensor_device_id: 'flexgrid-a', role: 'right' });
    assert.equal(p.text, 'Ready: 1 band (right arm) + finger labeler. Press Record.');
    assert.match(p.summary, /^1H \| flexgrid-a \(right\) \| labels: LASK5 lask5-1 \| win 100ms$/);
});

test('an untagged band that is stale is ignored when one tagged band is live', () => {
    const p = plan([band('flexgrid-a', 'left'), band('flexgrid-old', null, 30), lask()], 'lask5');
    assert.equal(p.ready, true);
    assert.equal(p.body.sensor_device_id, 'flexgrid-a');
});

test('vr: two bands with both hands, and a lone band', () => {
    const two = plan([band('flexgrid-a', 'left'), band('flexgrid-b', 'right'), quest('quest-left'), quest('quest-right')], 'vr');
    assert.equal(two.mode, '2h-vr');
    assert.equal(two.endpoint, '/api/recording/bilateral');
    assert.equal(two.text, 'Ready: 2 bands (left arm, right arm) + headset hand data. Press Record.');
    const one = plan([band('flexgrid-a'), quest()], 'vr');
    assert.equal(one.mode, '1h-vr');
    assert.equal(one.body.role, 'left');
});

test('webcam and gamepad paths', () => {
    const w = plan([band('flexgrid-a', 'left'), band('flexgrid-b', 'right'), cam()], 'webcam');
    assert.equal(w.mode, '2band-webcam');
    assert.equal(w.body.label_device_id, 'webcam-left');
    assert.equal(w.body.extra_sensors[0].device_id, 'flexgrid-b');
    const g = plan([band('flexgrid-a', 'left'), pad()], 'gamepad');
    assert.equal(g.mode, '1h-gamepad');
    assert.match(g.summary, /win 120ms/);
});

test('sensor only: ready text says it cannot be used to train', () => {
    const p = plan([band('flexgrid-a', 'left')], 'none');
    assert.equal(p.ready, true);
    assert.equal(p.mode, 'sensor-only');
    assert.equal(p.body.label_device_id, '');
    assert.match(p.text, /cannot be used to train/);
});

test('window override shows in the technical summary', () => {
    const p = plan([band('flexgrid-a', 'left'), lask()], 'lask5', { windowMs: 80 });
    assert.match(p.summary, /win 80ms$/);
});

test('advanced device pick overrides the label source logic', () => {
    const p = plan([], 'lask5', { sensorId: 'flexgrid-z', labelId: '__none__' });
    assert.equal(p.ready, true);
    assert.equal(p.mode, 'advanced');
    assert.deepEqual(p.body, { filename: null, sensor_device_id: 'flexgrid-z', label_device_id: '' });
});

// ---- autoLabelSource ----
test('autoLabelSource: bands + LASK5 streaming picks the finger labeler', () => {
    assert.equal(G.autoLabelSource([band('b', 'left'), lask()], 'lask5', false), 'lask5');
    assert.equal(G.autoLabelSource([band('b', 'left'), lask()], 'vr', false), 'lask5');
});

test('autoLabelSource: only a gamepad or only a webcam switches the default', () => {
    assert.equal(G.autoLabelSource([band('b', 'left'), pad()], 'lask5', false), 'gamepad');
    assert.equal(G.autoLabelSource([band('b', 'left'), cam()], 'lask5', false), 'webcam');
    assert.equal(G.autoLabelSource([quest()], 'lask5', false), 'vr');
});

test('autoLabelSource: priority lask5, webcam, headset, gamepad', () => {
    assert.equal(G.autoLabelSource([pad(), quest(), cam(), lask()], null, false), 'lask5');
    assert.equal(G.autoLabelSource([pad(), quest(), cam()], null, false), 'webcam');
    assert.equal(G.autoLabelSource([pad(), quest()], null, false), 'vr');
});

test('autoLabelSource: a second labeler does not steal a streaming pick', () => {
    assert.equal(G.autoLabelSource([cam(), lask()], 'webcam', false), 'webcam');
});

test('autoLabelSource: nothing streaming defaults to LASK5', () => {
    assert.equal(G.autoLabelSource([], null, false), 'lask5');
    assert.equal(G.autoLabelSource([], undefined, false), 'lask5');
    assert.equal(G.autoLabelSource([band('b', 'left')], 'keyboard', false), 'lask5');
    assert.equal(G.autoLabelSource([lask(9)], '', false), 'lask5');   // stale labeler is not streaming
});

test('autoLabelSource: a manual choice is never overridden', () => {
    assert.equal(G.autoLabelSource([lask()], 'vr', true), 'vr');
    assert.equal(G.autoLabelSource([], 'none', true), 'none');
    assert.equal(G.autoLabelSource([cam()], 'gamepad', true), 'gamepad');
});

test('device freshness threshold', () => {
    assert.equal(G.deviceFresh({ last_seen_age: null }), true);
    assert.equal(G.deviceFresh({}), true);
    assert.equal(G.deviceFresh({ last_seen_age: 1.9 }), true);
    assert.equal(G.deviceFresh({ last_seen_age: 2.0 }), false);
});

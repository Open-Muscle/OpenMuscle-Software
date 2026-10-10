// Node unit tests for static/train-card.js (result card + plain Models table).
// Run: node --test pc/tests/js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const T = require(path.join(__dirname, '..', '..', 'src', 'openmuscle', 'web', 'static', 'train-card.js'));

const verdict = (grade, extra) => Object.assign({
    grade,
    headline: { good: 'Good: explains about 82% of finger movement', fair: 'Fair: explains about 61% of finger movement',
        weak: 'Weak: explains about 30% of finger movement', not_working: 'Not working yet', untested: 'Not tested yet' }[grade],
    detail: 'Tested on 1 recording it never saw.',
    next_step: 'Record more takes and train again.',
    show_live_ok: grade === 'good' || grade === 'fair',
    score_pct: grade === 'not_working' ? 0 : 61,
}, extra || {});

const result = (grade, extra) => Object.assign({
    verdict: verdict(grade),
    active: true, activate_error: null, warning: null,
    model_path: '/data/models/m_1/model.pkl', model_dir: '/data/models/m_1',
    checks: [], bad_captures: [],
    metrics: {
        curl_r2: 0.61, curl_mae: 0.08, eval_method: 'held_out_capture', n_rows: 900, n_eval_rows: 300,
        n_features: 60, n_labels: 4, mse: 0.0123, r2: 0.58, r2_random_split: 0.97,
        per_finger: [{ finger: 'index', r2: 0.8 }, { finger: 'middle', r2: 0.4 }, { finger: 'ring', r2: 0.1 },
            { finger: 'pinky', r2: null }],
    },
}, extra || {});

test('tone never goes green for weak or not working', () => {
    assert.equal(T.toneOf('good'), 'good');
    assert.equal(T.toneOf('fair'), 'fair');
    assert.equal(T.toneOf('weak'), 'weak');
    assert.equal(T.toneOf('not_working'), 'bad');
    assert.equal(T.toneOf('untested'), 'untested');
    assert.equal(T.toneOf('anything else'), 'untested');
});

test('prediction auto-starts only for fair and good', () => {
    for (const [g, want] of [['good', true], ['fair', true], ['weak', false], ['not_working', false], ['untested', false]]) {
        assert.equal(T.shouldAutoStart(result(g)), want, g);
    }
});

test('prediction does not auto-start when the model was not loaded or complained', () => {
    assert.equal(T.shouldAutoStart(result('good', { active: false })), false);
    assert.equal(T.shouldAutoStart(result('good', { activate_error: 'wrong sensor layout' })), false);
    assert.equal(T.shouldAutoStart(result('good', { warning: 'bilateral pivot model' })), false);
});

test('a not-working result is a red card with Start anyway and no auto-start', () => {
    const c = T.cardModel(result('not_working', { metrics: Object.assign(result('not_working').metrics, { curl_r2: -0.4 }) }));
    assert.equal(c.tone, 'bad');
    assert.equal(c.autoStart, false);
    assert.equal(c.canStartAnyway, true);
    assert.match(c.headline, /Not working yet/);
    assert.equal(c.status, 'Model saved but not started.');
});

test('a weak result that paused a running prediction says so', () => {
    const c = T.cardModel(result('not_working', { paused_running: true }));
    assert.match(c.status, /^Model saved but not started\. Prediction was paused/);
});

test('a good result starts by itself and offers no Start anyway', () => {
    const c = T.cardModel(result('good'));
    assert.equal(c.tone, 'good');
    assert.equal(c.autoStart, true);
    assert.equal(c.canStartAnyway, false);
});

test('raw R2 and MSE appear only in the technical details', () => {
    const c = T.cardModel(result('fair'));
    const visible = [c.headline, c.detail, c.nextStep, c.status, c.title, ...c.notes].join(' ');
    assert.doesNotMatch(visible, /R²|R2|MSE|n_samples/);
    const tech = c.technical.map(r => r.join(' ')).join(' ');
    assert.match(tech, /R²/);
    assert.match(tech, /900/);
    assert.match(tech, /60 × 4/);
});

test('per-finger bars: percent, tone and a flat finger', () => {
    const bars = T.fingerBars(result('fair').metrics);
    assert.deepEqual(bars.map(b => b.name), ['Index', 'Middle', 'Ring', 'Pinky']);
    assert.deepEqual(bars.map(b => b.pct), [80, 40, 10, null]);
    assert.deepEqual(bars.map(b => b.tone), ['good', 'fair', 'bad', 'flat']);
    assert.match(bars[3].note, /did not move/);
});

test('negative finger scores draw an empty bar, never a negative width', () => {
    const bars = T.fingerBars({ per_finger: [{ finger: 'index', r2: -2.5 }] });
    assert.equal(bars[0].pct, 0);
    assert.equal(bars[0].tone, 'bad');
});

test('activation failure is said out loud and blocks Start anyway', () => {
    const c = T.cardModel(result('good', { active: false, activate_error: 'model expects 120 features' }));
    assert.match(c.status, /could not be loaded: model expects 120 features/);
    assert.equal(c.canStartAnyway, false);
    assert.equal(c.autoStart, false);
});

test('warnings from the data checks and bad takes are carried as notes', () => {
    const c = T.cardModel(result('fair', {
        checks: [{ level: 'warn', code: 'mixed_bands', message: 'Two band units were mixed.' }],
        bad_captures: [{ name: 'take3.csv', reasons: ['band looked flat'] }],
    }));
    assert.deepEqual(c.notes, ['Two band units were mixed.']);
    assert.deepEqual(c.badTakes, ['take3.csv (band looked flat)']);
});

test('saved-to prefers the model folder', () => {
    assert.equal(T.cardModel(result('fair')).savedTo, '/data/models/m_1');
    assert.equal(T.cardModel(result('fair', { model_dir: null })).savedTo, '/data/models/m_1/model.pkl');
});

test('two-hand results make one card per hand, from the server shape or the client shape', () => {
    const server = { per_hand: true, results: { left: result('good'), right: result('weak') } };
    const a = T.cardsFor(server);
    assert.deepEqual(a.map(c => c.title), ['Left hand', 'Right hand']);
    assert.equal(T.allAutoStart(a), false);
    const client = T.cardsFor({ left: result('good'), right: result('fair') });
    assert.equal(T.allAutoStart(client), true);
    assert.equal(T.cardsFor(result('fair')).length, 1);
    assert.deepEqual(T.cardsFor(null), []);
});

// ---- Models table ----

const model = (extra) => Object.assign({
    name: 'random_forest', dir: 'random_forest_20261009_143015_123_left', created: '20261009_143015',
    metrics: { role: 'left', n_features: 60, n_labels: 4, mse: 0.02, curl_r2: 0.55 },
    verdict: verdict('fair'), participant: 'p-07', session_name: 'Day one',
    band_device_ids: { left: ['fg-aaa'] },
}, extra || {});

test('model row speaks plainly', () => {
    const r = T.modelRow(model());
    assert.equal(r.name, 'random_forest_20261009_143015_123_left');
    assert.equal(r.when, '2026-10-09 14:30');
    assert.equal(r.hand, 'Left band');
    assert.equal(r.participant, 'p-07');
    assert.equal(r.score, 'Fair (61%)');
    assert.equal(r.tone, 'fair');
    assert.deepEqual(r.units, ['fg-aaa']);
    assert.equal(r.featuresLabels, '60 × 4');
});

test('hand label: left, right, or one band', () => {
    assert.equal(T.handLabel(model()), 'Left band');
    assert.equal(T.handLabel(model({ metrics: { role: 'right' } })), 'Right band');
    assert.equal(T.handLabel(model({ metrics: {} })), 'One band');
    assert.equal(T.handLabel(model({ metrics: { role: null } })), 'One band');
});

test('models with no stamp say unknown participant', () => {
    const r = T.modelRow(model({ participant: '' }));
    assert.equal(r.participant, 'unknown participant');
    assert.equal(r.unknownParticipant, true);
    assert.equal(T.modelRow(model({ participant: undefined })).participant, 'unknown participant');
});

test('score words for each grade, and no percentage for not working or untested', () => {
    assert.equal(T.scoreText({ verdict: verdict('good', { score_pct: 83 }) }), 'Good (83%)');
    assert.equal(T.scoreText({ verdict: verdict('weak', { score_pct: 31 }) }), 'Weak (31%)');
    assert.equal(T.scoreText({ verdict: verdict('not_working') }), 'Not working yet');
    assert.equal(T.scoreText({ verdict: verdict('untested', { score_pct: null }) }), 'Not tested yet');
    assert.equal(T.scoreText({}), 'Not tested yet');
});

test('a legacy model row has details but its score words never show R2', () => {
    const r = T.modelRow(model({ metrics: { n_features: 64, r2: 0.9, mse: 0.001 }, verdict: verdict('untested', { score_pct: null }) }));
    assert.equal(r.score, 'Not tested yet');
    assert.equal(r.r2, '0.900');           // only in the details columns
    assert.equal(r.mse, '0.0010');
});

test('participant codes compare without case or spacing', () => {
    assert.equal(T.sameParticipant(' P-07 ', 'p-07'), true);
    assert.equal(T.sameParticipant('p-07', 'p-08'), false);
    assert.equal(T.sameParticipant('', ''), true);
});

test('guard prompt carries each sentence and the two choices', () => {
    const text = T.guardPrompt([{ message: 'Trained on p-07; the session is p-08.' },
        { message: 'Band fg-b is on the left arm now.' }]);
    assert.match(text, /Trained on p-07; the session is p-08\./);
    assert.match(text, /Band fg-b is on the left arm now\./);
    assert.match(text, /OK to run it anyway/);
    assert.match(text, /Cancel/);
});

test('created stamps that are not the registry format pass through', () => {
    assert.equal(T.whenText('20260101_000000'), '2026-01-01 00:00');
    assert.equal(T.whenText('yesterday'), 'yesterday');
    assert.equal(T.whenText(undefined), '');
});

test('the participant code shows as it was typed', () => {
    const r = T.modelRow(model({ participant: 'p-07', participant_label: 'P-07' }));
    assert.equal(r.participant, 'P-07');
    assert.equal(r.unknownParticipant, false);
    assert.equal(T.participantCode({ participant: 'p-07' }), 'p-07');   // older model: the key
    assert.equal(T.participantCode({ participant: '', participant_label: '' }), '');
    // comparing still ignores case
    assert.equal(T.sameParticipant('P-07', 'p-07'), true);
});

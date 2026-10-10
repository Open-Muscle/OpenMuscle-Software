// Node unit tests for static/rec-status.js (recording status + post-take wording).
// Run: node --test pc/tests/js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const S = require(path.join(__dirname, '..', '..', 'src', 'openmuscle', 'web', 'static', 'rec-status.js'));

const LIMITS = { min_seconds: 10, min_rows: 50, recommended_seconds: 30 };
const BANNED = [/paired rows/i, /unpaired/i, /label pkts/i, /junk/i];

const flag = (code, severity) => ({ code, severity, word: code, reason: 'server reason for ' + code });
const rec = (quality, extra) => Object.assign({
    filename: 'a-take1.csv', rows: 120, duration_s: 7.4, sensor_device_id: 'fg-1',
    label_device_id: 'lask5-1', matched: 80, sensor_frames_seen: 100, match_rate: 0.8,
    unpaired_sensor: 20, label_packets_seen: 300, window_ms: 100, sensors: { 'fg-1': 'left' },
    quality, limits: LIMITS,
}, extra || {});
const GOOD = { verdict: 'good', reasons: [], flags: [] };

function visibleText(st) {
    return [st.headline, st.hint || '', ...st.sentences].join('\n');
}

test('formatTimer', () => {
    assert.equal(S.formatTimer(0), '0:00');
    assert.equal(S.formatTimer(7), '0:07');
    assert.equal(S.formatTimer(83.9), '1:23');
    assert.equal(S.formatTimer(3725), '1:02:05');
    assert.equal(S.formatTimer(undefined), '0:00');
    assert.equal(S.formatTimer(-4), '0:00');
});

test('low match: Almost no finger data sentence, counters only in details', () => {
    const q = { verdict: 'bad', reasons: ['x'], flags: [flag('low_match', 'bad')] };
    const st = S.live(rec(q));
    assert.match(visibleText(st), /^.*Almost no finger data/ms);
    assert.equal(st.tone, 'bad');
    // the technical counters are in details, and not in the visible text
    assert.ok(st.details.some(d => /Frames matched with finger data: 80 of 100/.test(d)));
    assert.ok(!/80 of 100/.test(visibleText(st)));
    for (const re of BANNED) assert.ok(!re.test(st.details.join('\n') + visibleText(st)), String(re));
});

test('flat band: snug-on-arm sentence', () => {
    const q = { verdict: 'bad', reasons: ['x'], flags: [flag('weak_signal', 'bad')] };
    const st = S.live(rec(q));
    assert.match(visibleText(st), /snug on your arm/);
});

test('every flag code reads plainly and avoids the banned words', () => {
    const codes = ['empty', 'short', 'weak_signal', 'no_labels', 'low_match', 'idle_labeler', 'hand_lost'];
    for (const code of codes) {
        for (const sev of ['check', 'bad']) {
            const s = S.remedy(flag(code, sev), LIMITS);
            assert.ok(s.length > 20, code + ' ' + sev);
            for (const re of BANNED) assert.ok(!re.test(s), code + ' ' + String(re));
            assert.ok(!s.includes('\u2014'), 'no em dash');
        }
    }
    assert.equal(S.remedy(flag('something_new', 'check')), 'server reason for something_new');
});

test('good live take: no problem sentences, length hint follows the take', () => {
    const early = S.live(rec(GOOD, { duration_s: 4 }));
    assert.equal(early.sentences.length, 0);
    assert.match(early.hint, /Keep recording: at least 10 s/);
    assert.match(S.live(rec(GOOD, { duration_s: 15 })).hint, /Long enough to train on/);
    assert.match(S.live(rec(GOOD, { duration_s: 45 })).hint, /Plenty of data/);
    assert.equal(S.live(rec(GOOD, { limits: undefined })).hint, '');
    assert.equal(early.timer, '0:04');
    assert.equal(early.headline, 'Recording');
});

test('live status shows both bands in details', () => {
    const st = S.live(rec(GOOD, { sensors: { 'fg-1': 'left', 'fg-2': 'right' } }));
    assert.ok(st.details.some(d => d === 'Bands: left fg-1, right fg-2'));
});

test('saved good take', () => {
    const st = S.saved(rec(GOOD, { duration_s: 42, too_short: false }));
    assert.equal(st.headline, 'Saved: Good');
    assert.deepEqual(st.sentences, ['This take looks good.']);
    assert.equal(st.tooShort, false);
});

test('saved short take says it is too short and states the advice once', () => {
    const q = { verdict: 'check', reasons: ['r'], flags: [flag('short', 'check')] };
    const note = 'Too short to train on (6 s). Record at least 10 s; 30 s or more works best.';
    const st = S.saved(rec(q, { duration_s: 6, too_short: true, short_note: note }));
    assert.equal(st.tooShort, true);
    assert.equal(st.headline, 'Saved: Check');
    assert.equal(st.sentences[0],
        'Saved, but too short to train on (6 s). Record at least 10 s; 30 s or more works best.');
    assert.equal(st.sentences.length, 1, 'the short flag is not repeated');
    for (const re of BANNED) assert.ok(!re.test(visibleText(st)));
});

test('saved bad take keeps the persisted verdict word', () => {
    const q = { verdict: 'bad', reasons: ['r'], flags: [flag('weak_signal', 'bad')] };
    const st = S.saved(rec(q, { too_short: false }));
    assert.equal(st.headline, 'Saved: Bad');
    assert.match(st.sentences[0], /snug/);
});

test('an older server without flags falls back to its reasons', () => {
    const st = S.live(rec({ verdict: 'check', reasons: ['Labels matched only 80% of frames.'] }));
    assert.deepEqual(st.sentences, ['Labels matched only 80% of frames.']);
});

// ---- collapsed result card (sticky bar must never hide the page) ----

test('collapsed summary keeps the verdict word and all three actions', () => {
    for (const verdict of ['good', 'check', 'bad']) {
        const q = { verdict, reasons: [], flags: [] };
        const sum = S.savedSummary(rec(q, { too_short: false }));
        assert.equal(sum.word, S.WORD[verdict]);
        assert.equal(sum.headline, 'Saved: ' + S.WORD[verdict]);
        assert.deepEqual(sum.actions.map(a => a.id),
            ['rec-next-take', 'rec-show-file', 'rec-go-train']);
        for (const a of sum.actions) {
            assert.ok(a.label && a.label.length > 0, 'every action has a label');
            assert.ok(a.title && a.title.length > 0, 'every action has a tooltip');
        }
        assert.match(sum.actions[0].label, /Record next take/);
        assert.match(sum.actions[1].label, /Show file/);
        assert.match(sum.actions[2].label, /Go to Train/);
    }
});

test('go-to-train tooltip says why a too-short take is not ticked', () => {
    const short = S.savedActions({ too_short: true });
    const ok = S.savedActions({ too_short: false });
    assert.match(short[2].title, /Too short/);
    assert.doesNotMatch(ok[2].title, /Too short/);
});

test('savedActions tolerates a missing result', () => {
    assert.equal(S.savedActions(undefined).length, 3);
    assert.equal(S.savedSummary(undefined).word, 'Good');
});

test('auto collapse waits 8 s and yields to Details and to a hand unfold', () => {
    assert.equal(S.COLLAPSE_AFTER_MS, 8000);
    assert.equal(S.shouldAutoCollapse({}), true);
    assert.equal(S.shouldAutoCollapse({ collapsed: true }), false);
    assert.equal(S.shouldAutoCollapse({ detailsOpen: true }), false);
    assert.equal(S.shouldAutoCollapse({ pinnedOpen: true }), false);
    assert.equal(S.shouldAutoCollapse(undefined), true);
});

// Node unit tests for static/participant.js (participant code warning + wording).
// Run: node --test pc/tests/js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const P = require(path.join(__dirname, '..', '..', 'src', 'openmuscle', 'web', 'static', 'participant.js'));

test('codes do not trigger the name warning', () => {
    for (const v of ['', '   ', 'P01', 'p-017', 'S001', 'W01', 'self', 'user_12', 'A1B2', 'ID-2024-03']) {
        assert.equal(P.looksLikeName(v), false, JSON.stringify(v));
        assert.equal(P.nameWarning(v), '');
    }
});

test('a space triggers the warning', () => {
    for (const v of ['Jane Smith', 'jane smith', 'P01 left', ' Jane  Smith ', 'Mr Smith']) {
        assert.equal(P.looksLikeName(v), true, JSON.stringify(v));
        assert.match(P.nameWarning(v), /looks like a name/);
    }
});

test('two capitalised words run together trigger the warning', () => {
    for (const v of ['JaneSmith', 'Jane_Smith', 'Jane-Smith', 'Jane.Smith']) {
        assert.equal(P.looksLikeName(v), true, JSON.stringify(v));
    }
});

test('an e-mail address triggers the warning', () => {
    assert.equal(P.looksLikeName('jane@example.org'), true);
});

test('null and undefined are treated as empty', () => {
    assert.equal(P.looksLikeName(null), false);
    assert.equal(P.looksLikeName(undefined), false);
});

test('the warning offers a code and says the value still saves', () => {
    assert.match(P.WARNING, /P01/);
    assert.match(P.WARNING, /still save/);
});

test('codeOf prefers wearer and falls back to subject', () => {
    assert.equal(P.codeOf({ wearer: 'P01', subject: 'old' }), 'P01');
    assert.equal(P.codeOf({ wearer: '', subject: 'S2' }), 'S2');
    assert.equal(P.codeOf({}), '');
    assert.equal(P.codeOf(null), '');
});

test('armText spells all three arms and nothing else', () => {
    assert.equal(P.armText('left'), 'left arm');
    assert.equal(P.armText('right'), 'right arm');
    assert.equal(P.armText('both'), 'both arms');
    assert.equal(P.armText(null), '');
    assert.equal(P.armText(''), '');
});

test('placeholder and helper wording', () => {
    assert.equal(P.PLACEHOLDER, 'P01 (a code, not a name)');
    assert.match(P.HELPER, /do not type names/);
});

// ---- a session closed when OpenMuscle stopped ----

test('the closed-on-restart notice names the newest past session', () => {
    const past = [{ id: 'a', name: 'Morning run', closed_on_restart: true }, { id: 'b' }];
    const note = P.closedNotice(past);
    assert.match(note, /Morning run/);
    assert.match(note, /closed when OpenMuscle stopped/);
    assert.match(note, /Past sessions/);
});

test('no notice unless the newest past session was closed on restart', () => {
    assert.equal(P.closedNotice([]), '');
    assert.equal(P.closedNotice(undefined), '');
    assert.equal(P.closedNotice([{ id: 'new' }, { id: 'old', closed_on_restart: true }]), '');
});

test('the notice falls back to the id when a session has no name', () => {
    assert.match(P.closedNotice([{ id: '2026-10-09T10-00-00', closed_on_restart: true }]), /2026-10-09T10-00-00/);
});

test('wasClosedOnRestart is false for ordinary sessions', () => {
    assert.equal(P.wasClosedOnRestart({ id: 'x' }), false);
    assert.equal(P.wasClosedOnRestart(null), false);
    assert.equal(P.wasClosedOnRestart({ closed_on_restart: true }), true);
    assert.equal(P.CLOSED_TAG, 'closed when OpenMuscle stopped');
});

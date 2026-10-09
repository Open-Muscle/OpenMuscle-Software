// Node unit tests for static/setup/wizard.js (the device setup wizard's wording
// and step machine). Run: node --test pc/tests/js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.join(__dirname, '..', '..', 'src', 'openmuscle', 'web', 'static');
const W = require(path.join(STATIC, 'setup', 'wizard.js'));
const T = W.TEXT;

const BANNED = /\b(V4|probe|probing|UDP|subnet|SSID)\b/i;

// ---- wording ----
function allStrings(node, out) {
    out = out || [];
    if (typeof node === 'string') out.push(node);
    else if (typeof node === 'function') out.push(String(node('Home', 'Band flexgrid-d7af0b', 'a band')));
    else if (Array.isArray(node)) node.forEach(n => allStrings(n, out));
    else if (node && typeof node === 'object') Object.values(node).forEach(n => allStrings(n, out));
    return out;
}

test('no banned words in any wording', () => {
    const bad = allStrings(T).filter(s => BANNED.test(s));
    assert.deepEqual(bad, []);
});

test('no banned words in the visible text of the setup page', () => {
    const html = fs.readFileSync(path.join(STATIC, 'setup', 'index.html'), 'utf8')
        .replace(/<!--[\s\S]*?-->/g, '')
        .replace(/<(script|style)[\s\S]*?<\/\1>/gi, '')
        .replace(/<[^>]+>/g, ' ');
    assert.ok(/Set up a device/.test(html));
    assert.ok(!BANNED.test(html), String(html.match(BANNED)));
});

test('no banned words in sentence-like string literals of setup.js', () => {
    const src = fs.readFileSync(path.join(STATIC, 'setup', 'setup.js'), 'utf8')
        .replace(/\/\/.*$/gm, '');
    const lits = (src.match(/'[^'\n]*'|"[^"\n]*"/g) || []).filter(l => /\s/.test(l));
    assert.deepEqual(lits.filter(l => BANNED.test(l)), []);
});

test('dashboard entry points use plain words', () => {
    const idx = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
    assert.ok(idx.includes('href="/setup"'));
    const app = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
    assert.ok(!app.includes('Waiting for a flexgrid'));
    assert.ok(!app.includes('No V4 sources'));
});

test('checklist about 2.4 GHz comes first', () => {
    assert.match(T.prepareChecks[0], /2\.4 GHz/);
    assert.match(T.prepareChecks[2], /sign in/);
    const s = W.initial({});
    assert.equal(s.step, 'prepare');
});

// ---- helpers ----
test('osFromUserAgent', () => {
    assert.equal(W.osFromUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64)'), 'windows');
    assert.equal(W.osFromUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'), 'mac');
    assert.equal(W.osFromUserAgent('Mozilla/5.0 (X11; Linux x86_64)'), 'linux');
    assert.equal(W.osFromUserAgent(''), 'linux');
});

test('suggestion drops device networks and blanks', () => {
    assert.equal(W.suggestion('HomeNet'), 'HomeNet');
    assert.equal(W.suggestion('OM-flexgrid-d7af0b'), '');
    assert.equal(W.suggestion(null), '');
    assert.equal(W.suggestion('  '), '');
});

test('idTail and deviceTitle', () => {
    assert.equal(W.idTail('flexgrid-d7af0b'), 'd7af0b');
    assert.equal(W.deviceTitle('flexgrid-d7af0b', 'flexgrid'), 'Band flexgrid-d7af0b');
    assert.equal(W.deviceTitle('lask5-01', 'lask5'), 'Labeler lask5-01');
    assert.equal(W.deviceTitle('x-1', 'mystery'), 'Device x-1');
});

test('validateWifi mirrors the device limits in bytes', () => {
    assert.equal(W.validateWifi('', 'password1'), T.errName);
    assert.equal(W.validateWifi('a'.repeat(33), 'password1'), T.errNameLong);
    assert.equal(W.validateWifi('é'.repeat(17), 'password1'), T.errNameLong);   // 34 bytes
    assert.equal(W.validateWifi('Home', 'short'), T.errPasswordShort);
    assert.equal(W.validateWifi('Home', 'x'.repeat(64)), T.errPasswordLong);
    assert.equal(W.validateWifi('Home', ''), null);                                   // open network
    assert.equal(W.validateWifi('My Home WiFi', 'correct horse'), null);
});

test('errorMessage maps codes and falls back', () => {
    assert.equal(W.errorMessage('unreachable'), T.errReach);
    assert.equal(W.errorMessage('wrong_device'), T.errWrongDevice);
    assert.equal(W.errorMessage('zzz', 'Plain text'), 'Plain text');
    assert.equal(W.errorMessage('zzz'), T.errGeneric);
});

test('networkOptions dedupes and sorts by strength', () => {
    const o = W.networkOptions([
        { ssid: 'B', rssi: -80 }, { ssid: 'A', rssi: -50 }, { ssid: 'B', rssi: -40 }, { ssid: '', rssi: -10 },
    ]);
    assert.deepEqual(o.map(x => x.name), ['B', 'A']);
    assert.equal(W.signalWord(-50), 'strong');
    assert.equal(W.signalWord(-90), 'weak');
});

// ---- step machine ----
function pass(s, evs) { return evs.reduce((st, e) => W.reduce(st, e), s); }
const probeOk = (id, dev, st) => ({ type: 'probe', result: { reachable: true, info: { id, dev: dev || 'flexgrid', state: st || 'unprovisioned', ap_name: 'OM-x' } } });

test('happy path reaches the send step from the start', () => {
    let s = W.initial({ os: 'windows', hint: 'HomeNet' });
    s = pass(s, [{ type: 'next' }, { type: 'pick', kind: 'band' }, probeOk('flexgrid-d7af0b'), { type: 'confirmId', yes: true }]);
    assert.equal(s.step, 'wifi');
    assert.equal(s.hint, 'HomeNet');
    s = W.reduce(s, { type: 'send', ssid: 'HomeNet', password: 'correct horse', remember: true });
    assert.equal(s.step, 'sending');
    s = W.reduce(s, { type: 'sendOk', id: 'flexgrid-d7af0b', since: 10, now: 1000 });
    assert.equal(s.step, 'wait');
    assert.equal(s.sentSsid, 'HomeNet');
    s = W.reduce(s, { type: 'found', id: 'flexgrid-d7af0b', ip: '10.0.0.23' });
    assert.equal(s.step, 'done');
    assert.equal(s.found.ip, '10.0.0.23');
});

test('probe is ignored outside the join step and unreachable clears an unconfirmed match', () => {
    const s0 = W.initial({});
    assert.equal(W.reduce(s0, probeOk('a')), s0);
    let s = pass(s0, [{ type: 'next' }, { type: 'pick', kind: 'band' }, probeOk('flexgrid-aaaaaa')]);
    assert.equal(s.device.id, 'flexgrid-aaaaaa');
    s = W.reduce(s, { type: 'probe', result: { reachable: false } });
    assert.equal(s.device, null);
});

test('a device that is already on Wi-Fi is not accepted', () => {
    const s = pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'band' }, probeOk('flexgrid-aaaaaa', 'flexgrid', 'provisioned')]);
    assert.equal(s.device, null);
    assert.equal(s.notice, T.joinAlreadyOnline);
});

test('picking the wrong kind is corrected to what answered', () => {
    const s = pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'labeler' }, probeOk('flexgrid-aaaaaa', 'flexgrid')]);
    assert.equal(s.kind, 'band');
    assert.ok(s.notice.includes('a band'));
});

test('saying no to the device id keeps it from coming straight back', () => {
    let s = pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'band' }, probeOk('flexgrid-aaaaaa')]);
    s = W.reduce(s, { type: 'confirmId', yes: false });
    assert.equal(s.device, null);
    assert.equal(s.step, 'join');
    assert.equal(W.reduce(s, probeOk('flexgrid-aaaaaa')).device, null);
    assert.equal(W.reduce(s, probeOk('flexgrid-bbbbbb')).device.id, 'flexgrid-bbbbbb');
});

test('validation failure stays on the Wi-Fi step with a plain error', () => {
    let s = pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'band' }, probeOk('flexgrid-aaaaaa'), { type: 'confirmId', yes: true }]);
    s = W.reduce(s, { type: 'send', ssid: 'Home', password: 'short' });
    assert.equal(s.step, 'wifi');
    assert.equal(s.error.message, T.errPasswordShort);
    assert.equal(s.creds, null);
});

test('send failure returns to the Wi-Fi step with the mapped message', () => {
    let s = pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'band' }, probeOk('flexgrid-aaaaaa'),
        { type: 'confirmId', yes: true }, { type: 'send', ssid: 'Home', password: 'correct horse' }]);
    s = W.reduce(s, { type: 'sendFail', code: 'wrong_device', message: 'x' });
    assert.equal(s.step, 'wifi');
    assert.equal(s.error.message, T.errWrongDevice);
});

test('three devices in a row need the Wi-Fi password entered once', () => {
    let s = W.initial({ hint: 'HomeNet' });
    let typed = 0;
    s = pass(s, [{ type: 'next' }]);
    for (let i = 0; i < 3; i++) {
        const id = 'flexgrid-00000' + i;
        s = pass(s, [{ type: 'pick', kind: 'band' }, probeOk(id), { type: 'confirmId', yes: true }]);
        assert.equal(s.step, 'wifi');
        if (s.useSaved && s.creds) {
            s = W.reduce(s, { type: 'send', ssid: '', password: '' });          // "send the same details"
        } else {
            typed++;
            s = W.reduce(s, { type: 'send', ssid: 'HomeNet', password: 'correct horse', remember: true });
        }
        assert.equal(s.step, 'sending');
        s = pass(s, [{ type: 'sendOk', id, since: i, now: 1000 * i }, { type: 'found', id, ip: '10.0.0.' + (20 + i) }]);
        assert.equal(s.step, 'done');
        s = W.reduce(s, { type: 'nextDevice' });
        assert.equal(s.step, 'kind');                // prepare is skipped
    }
    assert.equal(typed, 1);
    assert.equal(s.count, 3);
});

test('not remembering clears the details after sending', () => {
    const s = pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'band' }, probeOk('flexgrid-aaaaaa'),
        { type: 'confirmId', yes: true }, { type: 'send', ssid: 'Home', password: 'correct horse', remember: false }]);
    assert.equal(s.creds, null);
    assert.equal(s.useSaved, false);
});

test('remembered details from the tab skip the Wi-Fi entry', () => {
    const s = W.initial({ remembered: { ssid: 'Home', password: 'correct horse' } });
    assert.equal(s.useSaved, true);
    const c = W.reduce(s, { type: 'changeWifi' });
    assert.equal(c.useSaved, false);
});

test('restart from the troubleshooting list drops the saved details and goes back to joining', () => {
    let s = W.initial({ remembered: { ssid: 'Home', password: 'correct horse' } });
    s = pass(s, [{ type: 'next' }, { type: 'pick', kind: 'band' }, probeOk('flexgrid-aaaaaa'), { type: 'confirmId', yes: true },
        { type: 'send', ssid: '', password: '' }, { type: 'sendOk', id: 'flexgrid-aaaaaa', now: 0 }]);
    s = W.reduce(s, { type: 'restart' });
    assert.equal(s.step, 'join');
    assert.equal(s.useSaved, false);
});

test('back moves one step and never past the first', () => {
    let s = pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'band' }]);
    s = W.reduce(s, { type: 'back' });
    assert.equal(s.step, 'kind');
    s = W.reduce(s, { type: 'back' });
    assert.equal(s.step, 'prepare');
    assert.equal(W.reduce(s, { type: 'back' }).step, 'prepare');
});

// ---- waiting ----
function waiting() {
    return pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'band' }, probeOk('flexgrid-aaaaaa'),
        { type: 'confirmId', yes: true }, { type: 'send', ssid: 'HomeNet', password: 'correct horse' },
        { type: 'sendOk', id: 'flexgrid-aaaaaa', since: 5, now: 100000 }]);
}

test('waitView ticks the checklist from the signals', () => {
    const s = waiting();
    let v = W.waitView(s, { probeAnswering: true, hintSsid: 'OM-flexgrid-aaaaaa', found: false }, 105000);
    assert.deepEqual(v.items.map(i => i.done), [false, false, false]);
    v = W.waitView(s, { probeAnswering: false, hintSsid: 'OM-flexgrid-aaaaaa', found: false }, 105000);
    assert.deepEqual(v.items.map(i => i.done), [true, false, false]);
    v = W.waitView(s, { probeAnswering: false, hintSsid: 'HomeNet', found: false }, 110000);
    assert.deepEqual(v.items.map(i => i.done), [true, true, false]);
    v = W.waitView(s, { found: true }, 112000);
    assert.deepEqual(v.items.map(i => i.done), [true, true, true]);
    assert.equal(v.timedOut, false);
});

test('waitView assumes the restart is over after about 25 seconds', () => {
    const v = W.waitView(waiting(), { probeAnswering: null }, 100000 + 26000);
    assert.equal(v.items[0].done, true);
});

test('the troubleshooting list opens at 60 seconds, not before, and never once found', () => {
    const s = waiting();
    assert.equal(W.waitView(s, {}, 100000 + 59000).timedOut, false);
    assert.equal(W.waitView(s, {}, 100000 + 60000).timedOut, true);
    assert.equal(W.waitView(s, { found: true }, 100000 + 90000).timedOut, false);
});

test('shouldScanNow starts at 20 s and repeats every 25 s', () => {
    assert.equal(W.shouldScanNow(10, null), false);
    assert.equal(W.shouldScanNow(20, null), true);
    assert.equal(W.shouldScanNow(30, 20), false);
    assert.equal(W.shouldScanNow(45, 20), true);
});

test('troubleshooting steps are in the agreed order and OS specific', () => {
    const ids = W.troubleshootingSteps('windows').map(s => s.id);
    assert.deepEqual(ids, ['password', 'band', 'network', 'firewall', 'isolation', 'address']);
    const fw = os => W.troubleshootingSteps(os).find(s => s.id === 'firewall').body;
    assert.match(fw('windows'), /Windows/);
    assert.match(fw('mac'), /Local Network/);
    assert.match(fw('linux'), /ufw/);
    assert.match(fw('anything'), /Windows/);
    assert.ok(T.doneForget.includes('Forget'));
});

test('describeDiagnose is tolerant', () => {
    assert.equal(W.describeDiagnose(null), null);
    assert.equal(W.describeDiagnose({}), null);
    const d = W.describeDiagnose({ likely_cause: 'firewall', checks: [{ name: 'Listener', ok: true }, { label: 'Rule', ok: false, fix: 'Add it' }, 7] });
    assert.match(d.summary, /firewall/);
    assert.deepEqual(d.lines, ['OK: Listener', 'Problem: Rule: Add it']);
    assert.equal(W.describeDiagnose({ likely_cause: 'unknown_thing' }), null);
});

// Node tests for the USB cable branch of the device setup wizard: the step
// machine in static/setup/wizard.js, and the page in static/setup/setup.js run
// against a small fake DOM and a fake fetch (no browser, no server, no serial
// port). Run: node --test pc/tests/js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.join(__dirname, '..', '..', 'src', 'openmuscle', 'web', 'static', 'setup');
const W = require(path.join(STATIC, 'wizard.js'));
const T = W.TEXT;

const BANNED = /\b(V4|probe|probing|UDP|subnet|SSID)\b/i;
const BAND = { port: 'COM5', description: 'USB Serial', answered: true, id: 'flexgrid-d7af0b', role: 'left', state: 'unprovisioned', fw: '1.2.3', wifi_ssid: '' };
const BAND2 = { port: 'COM6', description: 'USB Serial', answered: true, id: 'flexgrid-aa11bb', role: 'right', state: 'provisioned', fw: '1.2.3', wifi_ssid: 'Old Net' };
const SILENT = { port: 'COM7', description: 'USB Serial', answered: false, id: '', role: '', state: '', fw: '', wifi_ssid: '' };

// ---- wording ----
function allStrings(node, out) {
    out = out || [];
    if (typeof node === 'string') out.push(node);
    else if (typeof node === 'function') out.push(String(node('Home', 'Band flexgrid-d7af0b', 'a band')));
    else if (Array.isArray(node)) node.forEach(n => allStrings(n, out));
    else if (node && typeof node === 'object') Object.values(node).forEach(n => allStrings(n, out));
    return out;
}

test('the USB wording has no banned words and never says serial or port', () => {
    const usbKeys = Object.keys(T).filter(k => /^usb|^waitUsb|^waitSavedUsb/.test(k));
    assert.ok(usbKeys.length >= 10);
    const text = allStrings(usbKeys.map(k => T[k]).concat([T.troubleSteps.passwordUsb])).join(' ');
    assert.doesNotMatch(text, BANNED);
    assert.doesNotMatch(text, /\bserial\b|\bCOM\d|\bttyACM|\bJSON\b|\bbaud\b/i);
});

test('the quoted labels the guides use are in the wording', () => {
    assert.equal(T.usbTitle, 'Plug the band into this computer');
    assert.equal(T.usbOwn, 'Use the band\'s own Wi-Fi instead');
    assert.equal(T.wifiSend, 'Send to the device');
    assert.equal(T.kinds.band.title, 'A band');
    assert.match(T.usbSilent, /Unplug the cable and plug it back in \(or press the BOOT button\), then try again\./);
    assert.match(T.usbChoose, /More than one band is plugged in/);
});

// ---- helpers ----
test('usbBandLabel and roleWord', () => {
    assert.equal(W.usbBandLabel(BAND), 'Band flexgrid-d7af0b (left arm)');
    assert.equal(W.usbBandLabel(BAND2), 'Band flexgrid-aa11bb (right arm)');
    assert.equal(W.usbBandLabel({ id: 'flexgrid-000001', role: '' }), 'Band flexgrid-000001');
    assert.equal(W.roleWord('labeler'), 'labeler');
    assert.equal(W.roleWord('white'), '');
});

test('usbBandsFrom keeps the answering bands and counts the silent ones', () => {
    const r = W.usbBandsFrom({ ports: [BAND, SILENT, BAND2, null] });
    assert.deepEqual(r.bands.map(b => b.port), ['COM5', 'COM6']);
    assert.equal(r.silent, 1);
    assert.deepEqual(W.usbBandsFrom(null), { bands: [], silent: 0 });
    assert.deepEqual(W.usbBandsFrom({ ports: 'x' }), { bands: [], silent: 0 });
});

// ---- step machine ----
function pass(s, evs) { return evs.reduce((st, e) => W.reduce(st, e), s); }
const ports = list => ({ type: 'usbPorts', result: { ports: list } });

function atUsb(extra) {
    return pass(W.initial({ hint: 'HomeNet' }), [{ type: 'next' }, { type: 'pick', kind: 'band' }].concat(extra || []));
}

test('choosing a band goes to the USB step; the labeler and the own-Wi-Fi flag go to join', () => {
    let s = pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'band' }]);
    assert.equal(s.step, 'usb');
    assert.equal(s.kind, 'band');
    s = pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'labeler' }]);
    assert.equal(s.step, 'join');
    s = pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'band', setupNetwork: true }]);
    assert.equal(s.step, 'join');
});

test('one answering band is chosen by itself; several need a pick; a vanished pick clears', () => {
    let s = atUsb([ports([BAND, SILENT])]);
    assert.equal(s.usbPort, 'COM5');
    assert.equal(s.usbSilent, 1);
    s = W.reduce(atUsb(), ports([BAND, BAND2]));
    assert.equal(s.usbBands.length, 2);
    assert.equal(s.usbPort, '');
    s = W.reduce(s, { type: 'usbPick', port: 'COM6' });
    assert.equal(s.usbPort, 'COM6');
    assert.equal(W.reduce(s, { type: 'usbPick', port: 'COM99' }), s);       // not a listed band
    assert.equal(W.reduce(s, ports([BAND, BAND2])), s);                      // nothing changed: same state
    s = W.reduce(s, ports([BAND]));
    assert.equal(s.usbPort, 'COM5');                                         // COM6 left, the only one remains
    s = W.reduce(s, ports([]));
    assert.equal(s.usbPort, '');
    assert.equal(s.usbBands.length, 0);
});

test('usbPorts is ignored outside the USB step', () => {
    const s = W.initial({});
    assert.equal(W.reduce(s, ports([BAND])), s);
});

test('happy path: details go down the cable and hand over to the wait step by band id', () => {
    let s = atUsb([ports([BAND])]);
    s = W.reduce(s, { type: 'usbSend', ssid: 'HomeNet', password: 'correct horse', remember: true });
    assert.equal(s.step, 'sending');
    assert.equal(s.via, 'usb');
    assert.equal(s.device.id, 'flexgrid-d7af0b');
    assert.equal(s.device.port, 'COM5');
    assert.equal(s.sentSsid, 'HomeNet');
    s = W.reduce(s, { type: 'sendOk', id: 'flexgrid-d7af0b', since: 12.5, now: 1000 });
    assert.equal(s.step, 'wait');
    assert.equal(s.sentId, 'flexgrid-d7af0b');
    assert.equal(s.since, 12.5);
    s = W.reduce(s, { type: 'found', id: 'flexgrid-d7af0b', ip: '10.0.0.23' });
    assert.equal(s.step, 'done');
    assert.equal(s.found.ip, '10.0.0.23');
    assert.equal(s.count, 1);
});

test('sendOk takes the id the server reports, which wins over the listing', () => {
    let s = atUsb([ports([BAND])]);
    s = pass(s, [{ type: 'usbSend', ssid: 'HomeNet', password: 'correct horse' }, { type: 'sendOk', id: 'flexgrid-other', now: 0 }]);
    assert.equal(s.sentId, 'flexgrid-other');
});

test('sending without a band found, or with bad details, stays on the USB step with a plain error', () => {
    let s = W.reduce(atUsb(), { type: 'usbSend', ssid: 'HomeNet', password: 'correct horse' });
    assert.equal(s.step, 'usb');
    assert.equal(s.error.message, T.usbPickFirst);
    s = atUsb([ports([BAND])]);
    assert.equal(W.reduce(s, { type: 'usbSend', ssid: '', password: 'correct horse' }).error.message, T.errName);
    assert.equal(W.reduce(s, { type: 'usbSend', ssid: 'Home', password: 'short' }).error.message, T.errPasswordShort);
    assert.equal(W.reduce(s, { type: 'usbSend', ssid: 'Home', password: 'x'.repeat(64) }).error.message, T.errPasswordLong);
    assert.equal(W.reduce(s, { type: 'usbSend', ssid: 'a'.repeat(33), password: 'correct horse' }).error.message, T.errNameLong);
    assert.equal(W.reduce(s, { type: 'usbSend', ssid: 'Cafe', password: '' }).step, 'sending');   // open network
});

test('a failed send returns to the USB step, keeps the band list, and says why', () => {
    let s = atUsb([ports([BAND])]);
    s = pass(s, [{ type: 'usbSend', ssid: 'HomeNet', password: 'correct horse' }]);
    const failed = W.reduce(s, { type: 'sendFail', code: 'no_answer', message: T.usbSilent });
    assert.equal(failed.step, 'usb');
    assert.equal(failed.via, null);
    assert.equal(failed.error.message, T.usbSilent);
    assert.equal(failed.usbPort, 'COM5');
    const wrong = W.reduce(s, { type: 'sendFail', code: 'wrong_device', message: 'A different band answered on that USB port (flexgrid-x).' });
    assert.match(wrong.error.message, /flexgrid-x/);
    const rej = W.reduce(s, { type: 'sendFail', code: 'rejected', message: 'The band could not save the Wi-Fi details.' });
    assert.equal(rej.error.message, 'The band could not save the Wi-Fi details.');
    const off = W.reduce(s, { type: 'sendFail', code: 'offline', message: T.errOffline });
    assert.equal(off.step, 'usb');
});

test('the saved details are offered for the next band and used without retyping', () => {
    let s = atUsb([ports([BAND])]);
    s = pass(s, [{ type: 'usbSend', ssid: 'HomeNet', password: 'correct horse', remember: true },
        { type: 'sendOk', id: 'flexgrid-d7af0b', now: 0 }, { type: 'found', id: 'flexgrid-d7af0b', ip: '10.0.0.5' },
        { type: 'nextDevice' }]);
    assert.equal(s.step, 'kind');
    assert.equal(s.via, null);
    assert.equal(s.useSaved, true);
    s = pass(s, [{ type: 'pick', kind: 'band' }, ports([BAND2]), { type: 'usbSend', ssid: '', password: '' }]);
    assert.equal(s.step, 'sending');
    assert.equal(s.sentSsid, 'HomeNet');
    assert.equal(s.device.id, 'flexgrid-aa11bb');
    // forgetting leaves nothing behind
    const f = pass(atUsb([ports([BAND])]), [{ type: 'usbSend', ssid: 'Home', password: 'correct horse', remember: false }]);
    assert.equal(f.creds, null);
});

test('back from the USB step goes to Choose; own Wi-Fi leads to Join and back returns to USB', () => {
    let s = atUsb([ports([BAND])]);
    assert.equal(W.reduce(s, { type: 'back' }).step, 'kind');
    s = W.reduce(s, { type: 'useBandWifi' });
    assert.equal(s.step, 'join');
    assert.equal(s.kind, 'band');
    assert.equal(s.fromUsb, true);
    s = W.reduce(s, { type: 'back' });
    assert.equal(s.step, 'usb');
    assert.equal(s.fromUsb, false);
    // reached Join the normal way (labeler): back still goes to Choose
    const l = pass(W.initial({}), [{ type: 'next' }, { type: 'pick', kind: 'labeler' }, { type: 'back' }]);
    assert.equal(l.step, 'kind');
    assert.equal(W.reduce(W.initial({}), { type: 'useBandWifi' }).step, 'prepare');
});

test('the own-Wi-Fi road still works end to end', () => {
    let s = atUsb([ports([BAND]), { type: 'useBandWifi' }]);
    s = pass(s, [{ type: 'probe', result: { reachable: true, info: { id: 'flexgrid-d7af0b', dev: 'flexgrid', state: 'unprovisioned', ap_name: 'OM-flexgrid-d7af0b' } } },
        { type: 'confirmId', yes: true }]);
    assert.equal(s.step, 'wifi');
    s = pass(s, [{ type: 'send', ssid: 'HomeNet', password: 'correct horse' }, { type: 'sendOk', id: 'flexgrid-d7af0b', now: 0 }]);
    assert.equal(s.step, 'wait');
    assert.notEqual(s.via, 'usb');
    const failed = pass(atUsb([ports([BAND]), { type: 'useBandWifi' }, { type: 'probe', result: { reachable: true, info: { id: 'flexgrid-d7af0b', dev: 'flexgrid', state: 'unprovisioned' } } }, { type: 'confirmId', yes: true }, { type: 'send', ssid: 'HomeNet', password: 'correct horse' }, { type: 'sendFail', code: 'unreachable' }]), []);
    assert.equal(failed.step, 'wifi');                       // a failure on this road returns to its own form
});

// ---- waiting ----
function waitingUsb() {
    return pass(atUsb([ports([BAND])]), [{ type: 'usbSend', ssid: 'HomeNet', password: 'correct horse' },
        { type: 'sendOk', id: 'flexgrid-d7af0b', since: 5, now: 100000 }]);
}

test('the USB checklist has no rejoin step and ticks from time and the sighting', () => {
    const s = waitingUsb();
    let v = W.waitView(s, {}, 101000);
    assert.deepEqual(v.items.map(i => i.key), ['sent', 'restarted', 'found']);
    assert.deepEqual(v.items.map(i => i.done), [true, false, false]);
    v = W.waitView(s, {}, 106000);
    assert.deepEqual(v.items.map(i => i.done), [true, true, false]);
    v = W.waitView(s, { found: true }, 102000);
    assert.deepEqual(v.items.map(i => i.done), [true, true, true]);
    assert.equal(W.waitView(s, {}, 100000 + 59000).timedOut, false);
    assert.equal(W.waitView(s, {}, 100000 + 60000).timedOut, true);
    assert.equal(W.waitView(s, { found: true }, 100000 + 90000).timedOut, false);
    assert.ok(!v.items.some(i => /back on/.test(i.text)));
});

test('the troubleshooting list for a USB send starts with its own password step and returns to USB', () => {
    const steps = W.troubleshootingSteps('windows', 'usb');
    assert.deepEqual(steps.map(x => x.id), ['password', 'band', 'network', 'firewall', 'isolation', 'address']);
    assert.equal(steps[0].title, T.troubleSteps.passwordUsb.title);
    assert.equal(W.troubleshootingSteps('windows')[0].title, T.troubleSteps.password.title);
    const s = W.reduce(waitingUsb(), { type: 'restart' });
    assert.equal(s.step, 'usb');
    assert.equal(s.creds, null);
    assert.equal(s.useSaved, false);
    assert.equal(s.via, null);
});

// ---- the page, against a fake DOM ----
class El {
    constructor(tag) {
        this.tagName = String(tag).toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.attrs = {};
        this.listeners = {};
        this.style = {};
        this.value = '';
        this.checked = false;
        this.disabled = false;
        this.className = '';
        this._text = '';
        this.type = '';
    }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    setAttribute(k, v) { this.attrs[k] = v; if (k === 'type') this.type = v; if (k === 'disabled') this.disabled = true; }
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
    focus() {}
    get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n._root === true; }
    set textContent(v) {
        this.children.forEach(c => { c.parentNode = null; });
        this.children = [];
        this._text = '';
        if (v) this.appendChild(new Text(String(v)));
    }
    get textContent() { return this.children.map(c => c.textContent).join(''); }
}
class Text {
    constructor(t) { this.t = t; this.parentNode = null; }
    get textContent() { return this.t; }
}

function walk(el, fn) {
    fn(el);
    (el.children || []).forEach(c => { if (c instanceof El) walk(c, fn); });
}
function findAll(root, pred) { const out = []; walk(root, e => { if (pred(e)) out.push(e); }); return out; }
const flush = async () => { for (let i = 0; i < 12; i++) await new Promise(r => setImmediate(r)); };

function makePage(routes) {
    const card = new El('section'); card._root = true;
    const progress = new El('ol'); progress._root = true;
    for (let i = 0; i < 5; i++) progress.appendChild(new El('li'));
    const calls = [];
    const intervals = [];
    const store = {};
    const ctx = {
        OMWizard: W,
        navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
        sessionStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } },
        document: {
            getElementById: id => (id === 'card' ? card : id === 'progress' ? progress : null),
            createElement: t => new El(t),
            createTextNode: t => new Text(String(t)),
        },
        fetch: async (url, opts) => {
            const method = (opts && opts.method) || 'GET';
            const body = opts && opts.body ? JSON.parse(opts.body) : undefined;
            calls.push({ method, url, body });
            const r = routes(method, url, body) || { status: 200, data: {} };
            // the real wait route holds the request open; never answer it in a microtask loop
            if (url.indexOf('/api/setup/wait') === 0) await new Promise(res => setTimeout(res, 15));
            return { ok: r.status >= 200 && r.status < 300, status: r.status, json: async () => r.data };
        },
        setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
        clearInterval: id => { if (intervals[id - 1]) intervals[id - 1].fn = null; },
        setTimeout, clearTimeout, Date, URLSearchParams, JSON, Promise, Array, Object, Set, String, Number, Math, isNaN, console,
        scrollTo: () => {},
    };
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(STATIC, 'setup.js'), 'utf8'), ctx, { filename: 'setup.js' });
    return {
        card, calls, store,
        text: () => card.textContent,
        button: label => findAll(card, e => e.tagName === 'BUTTON' && e.textContent.indexOf(label) >= 0)[0],
        byId: id => findAll(card, e => e.attrs.id === id)[0],
        click: el => { assert.ok(el, 'element to click'); (el.listeners.click || []).forEach(fn => fn({})); },
        tick: async () => { intervals.filter(i => i.fn).forEach(i => i.fn()); await flush(); },
        flush,
    };
}

function routesFor(opts) {
    opts = opts || {};
    return (method, url, body) => {
        if (url === '/api/setup/wifi-hint') return { status: 200, data: { ssid: 'HomeNet' } };
        if (url === '/api/setup/usb/ports') return { status: 200, data: { ports: opts.ports || [] } };
        if (url === '/api/setup/usb/provision') return opts.provision ? opts.provision(body) : { status: 200, data: { ok: true, id: 'flexgrid-d7af0b', since: 50, wait_s: 60 } };
        if (url.indexOf('/api/setup/wait') === 0) return { status: 200, data: opts.found ? { found: true, device: { device_id: 'flexgrid-d7af0b', ip: '10.0.0.23', device_type: 'flexgrid' } } : { found: false } };
        if (url === '/api/setup/probe') return { status: 200, data: { reachable: false } };
        if (url === '/api/discovery/scan') return { status: 200, data: {} };
        return { status: 404, data: {} };
    };
}

async function toUsbStep(page) {
    await page.flush();
    page.click(page.button(T.prepareReady));
    await page.flush();
    page.click(findAll(page.card, e => e.className === 'kind')[0]);       // A band
    await page.flush();
}

test('page: the band path shows the USB step, finds the band, sends down the cable and finishes', async () => {
    const opts = { ports: [] };
    const page = makePage(routesFor(opts));
    await toUsbStep(page);
    assert.match(page.text(), /Plug the band into this computer/);
    assert.match(page.text(), /Looking for a band on the USB cable/);
    assert.ok(page.byId('usb-send').disabled, 'send is off until a band is found');
    assert.ok(page.calls.some(c => c.url === '/api/setup/usb/ports'));
    assert.equal(page.byId('wifi-name').value, 'HomeNet', 'prefilled from the computer\'s Wi-Fi');

    opts.ports = [BAND];
    await page.tick();
    assert.match(page.text(), /Found Band flexgrid-d7af0b \(left arm\)/);
    assert.match(page.text(), /end with d7af0b/);
    assert.equal(page.byId('usb-send').disabled, false);

    page.byId('wifi-name').value = 'My Home WiFi';
    page.byId('wifi-password').value = 'correct horse';
    page.click(page.byId('usb-send'));
    await page.flush();
    const post = page.calls.find(c => c.url === '/api/setup/usb/provision');
    assert.ok(post, 'provision was called');
    assert.deepEqual(post.body, { port: 'COM5', ssid: 'My Home WiFi', password: 'correct horse', expect_id: 'flexgrid-d7af0b' });
    assert.match(page.text(), /Saved on the band/);
    assert.match(page.text(), /will join "My Home WiFi"/);
    assert.ok(!page.calls.some(c => c.url === '/api/setup/probe'), 'no setup-network probing on this road');

    // the wait step asks for this band by id, with the server's since
    const wait = page.calls.find(c => c.url.indexOf('/api/setup/wait') === 0);
    assert.ok(wait, 'wait was called');
    assert.match(wait.url, /device_id=flexgrid-d7af0b/);
    assert.match(wait.url, /since=50/);

    opts.found = true;
    await page.tick();
    await new Promise(res => setTimeout(res, 80));      // the held-open wait call answers
    await page.flush();
    assert.match(page.text(), /Band flexgrid-d7af0b is online at 10\.0\.0\.23/);
    assert.doesNotMatch(page.text(), /Forget/, 'nothing to forget: no setup network was joined');
    assert.ok(page.button(T.doneNext));
    assert.ok(!JSON.stringify(page.store).includes('correct horse') || page.store['om-setup-wifi'], 'only the tab storage may hold it');
});

test('page: a failure names the problem, keeps what was typed, and offers the band again', async () => {
    const opts = { ports: [BAND], provision: () => ({ status: 504, data: { ok: false, code: 'no_answer', message: T.usbSilent } }) };
    const page = makePage(routesFor(opts));
    await toUsbStep(page);
    await page.tick();
    page.byId('wifi-name').value = 'My Home WiFi';
    page.byId('wifi-password').value = 'correct horse';
    page.click(page.byId('usb-send'));
    await page.flush();
    assert.match(page.text(), /Unplug the cable and plug it back in \(or press the BOOT button\), then try again\./);
    assert.match(page.text(), /Plug the band into this computer/);
    // the details are offered again, and "a different Wi-Fi" brings back the form as typed
    assert.match(page.text(), /Use the same Wi-Fi as before: "My Home WiFi"/);
    page.click(page.button(T.wifiChange));
    await page.flush();
    assert.equal(page.byId('wifi-name').value, 'My Home WiFi');
    assert.equal(page.byId('wifi-password').value, 'correct horse');
});

test('page: several bands need a choice before sending', async () => {
    const opts = { ports: [BAND, BAND2] };
    const page = makePage(routesFor(opts));
    await toUsbStep(page);
    await page.tick();
    assert.match(page.text(), /More than one band is plugged in/);
    assert.match(page.text(), /Band flexgrid-aa11bb \(right arm\)/);
    assert.ok(page.byId('usb-send').disabled);
    const radios = findAll(page.card, e => e.tagName === 'INPUT' && e.type === 'radio');
    assert.equal(radios.length, 2);
    radios[1].checked = true;
    (radios[1].listeners.change || []).forEach(fn => fn({}));
    assert.equal(page.byId('usb-send').disabled, false);
    assert.match(page.text(), /already set up for "Old Net"/);
});

test('page: a silent band tells the person to replug it', async () => {
    const page = makePage(routesFor({ ports: [SILENT] }));
    await toUsbStep(page);
    await page.tick();
    assert.match(page.text(), /Unplug the cable and plug it back in \(or press the BOOT button\), then try again\./);
    assert.ok(page.byId('usb-send').disabled);
});

test('page: the band\'s own Wi-Fi is still reachable and goes back', async () => {
    const page = makePage(routesFor({ ports: [] }));
    await toUsbStep(page);
    page.click(page.byId('usb-own'));
    await page.flush();
    assert.match(page.text(), /Join the device's own Wi-Fi/);
    assert.match(page.text(), /two quick blinks then a pause/);
    assert.ok(page.calls.some(c => c.url === '/api/setup/probe'));
    page.click(page.button(T.back));
    await page.flush();
    assert.match(page.text(), /Plug the band into this computer/);
});

test('page: a labeler goes straight to the setup-network road', async () => {
    const page = makePage(routesFor({}));
    await page.flush();
    page.click(page.button(T.prepareReady));
    await page.flush();
    page.click(findAll(page.card, e => e.className === 'kind')[1]);       // A finger labeler
    await page.flush();
    assert.match(page.text(), /Join the device's own Wi-Fi/);
});

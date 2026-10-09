// Smoke test for the export dialog and the "where are my files" panel in app.js.
// app.js is a browser script, so it is run in a vm sandbox with a stand-in DOM:
// every element is a recording stub, fetch is scripted, and the test drives the
// real functions (openExportModal, runExport, renderStorage) end to end.
//
// Run: node --test pc/tests/js

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP = path.join(__dirname, '..', '..', 'src', 'openmuscle', 'web', 'static', 'app.js');

function makeEl(id) {
    const el = {
        id, hidden: false, disabled: false, checked: false, value: '', textContent: '',
        innerHTML: '', className: '', dataset: {}, style: {}, children: [],
        classList: {
            _s: new Set(),
            add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
            toggle(c, on) { if (on === undefined ? !this._s.has(c) : on) this._s.add(c); else this._s.delete(c); },
            contains(c) { return this._s.has(c); },
        },
        attrs: {},
        setAttribute(k, v) { this.attrs[k] = v; },
        getAttribute(k) { return this.attrs[k]; },
        addEventListener() {}, removeEventListener() {},
        querySelectorAll() { return []; }, querySelector() { return null; },
        appendChild(c) { this.children.push(c); return c; }, remove() {},
        click() { this.clicked = true; }, focus() {}, select() {},
        scrollIntoView() {}, getContext() { return null; },
    };
    return el;
}

function load(fetchImpl) {
    const els = {};
    const getEl = (id) => (els[id] = els[id] || makeEl(id));
    const doc = {
        getElementById: getEl,
        querySelectorAll: () => [],
        querySelector: () => null,
        addEventListener() {},
        createElement: (tag) => makeEl(tag),
        body: makeEl('body'),
        execCommand: () => true,
    };
    const downloads = [];
    doc.createElement = (tag) => {
        const e = makeEl(tag);
        if (tag === 'a') e.click = () => downloads.push({href: e.href, name: e.download});
        return e;
    };
    const sandbox = {
        document: doc,
        window: {location: {protocol: 'http:', host: 'localhost:8000'}, isSecureContext: false,
                 addEventListener() {}},
        navigator: {},
        location: {protocol: 'http:', host: 'localhost:8000'},
        console: {log() {}, warn() {}, error() {}, info() {}},
        fetch: fetchImpl,
        WebSocket: function () { return {close() {}, send() {}}; },
        setTimeout: () => 0, setInterval: () => 0, clearTimeout() {}, clearInterval() {},
        requestAnimationFrame: () => 0, alert() {}, confirm: () => true,
        localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
        URL: {createObjectURL: () => 'blob:fake', revokeObjectURL() {}},
        performance: {now: () => 0},
        Image: function () {}, Blob: function () {},
    };
    sandbox.window.document = doc;
    vm.createContext(sandbox);
    const code = fs.readFileSync(APP, 'utf8') + `
;globalThis.__t = {openExportModal, runExport, renderStorage, loadStorage, fmtBytes,
    exportSession, refreshExportEstimate, setStorage: (s) => { storageInfo = s; },
    selected: selectedCaptures};`;
    vm.runInContext(code, sandbox, {filename: 'app.js'});
    return {t: sandbox.__t, els, getEl, downloads};
}

function jsonResponse(body, status = 200, headers = {}) {
    return Promise.resolve({
        ok: status < 400, status,
        headers: {get: (k) => headers[k] || headers[k.toLowerCase()] || null},
        json: async () => body, text: async () => JSON.stringify(body),
        clone() { return this; },
        blob: async () => ({size: 4321}),
    });
}

const EST = {takes: 2, estimated_zip_bytes: 6000, compact_zip_bytes: 6000,
             with_raw_zip_bytes: 9000, warn_over_bytes: 20971520, warn_large: false, warnings: []};

test('fmtBytes reads like a person would say it', () => {
    const {t} = load(() => jsonResponse({}));
    assert.strictEqual(t.fmtBytes(512), '512 B');
    assert.strictEqual(t.fmtBytes(2048), '2 KB');
    assert.strictEqual(t.fmtBytes(3.5 * 1024 * 1024), '3.5 MB');
    assert.strictEqual(t.fmtBytes(25 * 1024 * 1024), '25 MB');
    assert.strictEqual(t.fmtBytes(null), '?');
});

test('export dialog shows the estimate and downloads the zip', async () => {
    const calls = [];
    const {t, getEl, downloads} = load((url, init) => {
        calls.push({url, init});
        if (url === '/api/storage') return jsonResponse({exports: '/home/me/OpenMuscle/exports'});
        if (url.includes('/estimate')) return jsonResponse(EST);
        return jsonResponse({}, 200, {'Content-Disposition': 'attachment; filename="openmuscle-session-abc.zip"'});
    });
    await new Promise(r => setImmediate(r));          // the page's own storage fetch
    t.exportSession({id: 'abc', name: 'morning', capture_count: 2});
    await new Promise(r => setImmediate(r));
    const exportCalls = () => calls.filter(c => c.url.includes('/export'));
    assert.ok(exportCalls()[0].url.startsWith('/api/sessions/abc/export/estimate?raw=0&notes=0'),
              exportCalls()[0].url);
    assert.match(getEl('export-size').textContent, /about 5\.9 KB|about 6 KB/);
    assert.match(getEl('export-what').textContent, /Export session morning \(2 takes\)/);
    assert.strictEqual(getEl('export-raw').checked, false);        // raw streams off by default
    assert.strictEqual(getEl('export-notes').checked, false);
    assert.strictEqual(getEl('export-go').disabled, false);
    assert.strictEqual(getEl('export-warn').hidden, true);

    await t.runExport();
    assert.ok(exportCalls()[1].url.startsWith('/api/sessions/abc/export?raw=0&notes=0'),
              exportCalls()[1].url);
    assert.deepStrictEqual(downloads.map(d => d.name), ['openmuscle-session-abc.zip']);
    const msg = getEl('export-result').textContent;
    assert.match(msg, /openmuscle-session-abc\.zip/);
    assert.match(msg, /Downloads/);
    assert.match(msg, /\/home\/me\/OpenMuscle\/exports/);
    assert.match(msg, /Attach this file to an email/);
});

test('selected takes use the POST endpoints and send the options', async () => {
    const calls = [];
    const {t, getEl} = load((url, init) => {
        calls.push({url, init});
        return jsonResponse(url.includes('estimate') ? EST : {}, 200,
                            {'Content-Disposition': 'attachment; filename="x.zip"'});
    });
    t.openExportModal({captures: ['a.csv', 'b.csv'], title: 'Export 2 selected takes'});
    await new Promise(r => setImmediate(r));
    getEl('export-raw').checked = true;
    await t.refreshExportEstimate();
    await new Promise(r => setImmediate(r));
    const last = calls.filter(c => c.url.includes('/export')).pop();
    assert.strictEqual(last.url, '/api/export/estimate');
    assert.deepStrictEqual(JSON.parse(last.init.body),
                           {captures: ['a.csv', 'b.csv'], raw: true, notes: false});
});

test('a large estimate shows the over-20-MB warning and the smaller option', async () => {
    const big = Object.assign({}, EST, {estimated_zip_bytes: 40 * 1024 * 1024, warn_large: true});
    const {t, getEl} = load(() => jsonResponse(big));
    t.openExportModal({sessionId: 's1', title: 'Export'});
    await new Promise(r => setImmediate(r));
    const warn = getEl('export-warn');
    assert.strictEqual(warn.hidden, false);
    assert.match(warn.textContent, /over about 20 MB/);
    assert.match(warn.textContent, /one session at a time/);
});

test('an export error is shown in plain words and nothing is downloaded', async () => {
    const {t, getEl, downloads} = load((url) => {
        if (url.includes('/estimate')) return jsonResponse(EST);
        return jsonResponse({detail: 'A recording is in progress (t.csv). Stop it, then export.'}, 409);
    });
    t.exportSession({id: 's', name: 's', capture_count: 1});
    await new Promise(r => setImmediate(r));
    await t.runExport();
    assert.deepStrictEqual(downloads, []);
    assert.match(getEl('export-result').textContent, /Stop it, then export/);
    assert.match(getEl('export-result').className, /err/);
});

test('storage panel: path always shown, Open folder only when allowed, low-space warning', async () => {
    const info = {captures: '/data/captures', models: '/data/models', free_bytes: 500 * 1024 * 1024,
                  low_space: true, reveal_enabled: false, legacy_hint: null};
    const {t, getEl} = load(() => jsonResponse(info));
    const open = makeEl('open');
    const path1 = makeEl('p'); path1.dataset.storagePath = 'captures';
    const panel = getEl('storage-panel');
    panel.querySelectorAll = (sel) => sel === '[data-storage-path]' ? [path1]
        : sel === '[data-storage-open]' ? [open] : [];
    await t.loadStorage();
    assert.strictEqual(path1.textContent, '/data/captures');
    assert.strictEqual(open.hidden, true);                           // not on the server computer
    const note = getEl('storage-note');
    assert.match(note.textContent, /Only 500 MB free/);
    assert.ok(note.classList.contains('warn'));

    t.setStorage(Object.assign({}, info, {reveal_enabled: true, low_space: false}));
    t.renderStorage();
    assert.strictEqual(open.hidden, false);
    assert.ok(!note.classList.contains('warn'));
});

// Device setup wizard: the page. The step logic and all wording are in
// wizard.js (window.OMWizard); this file draws them and talks to the
// loopback-only /api/setup/* routes. Everything is built with textContent, so a
// Wi-Fi name with odd characters can never inject markup.
(function () {
    'use strict';
    const W = window.OMWizard;
    const T = W.TEXT;
    const STORE_KEY = 'om-setup-wifi';

    const card = document.getElementById('card');
    const progress = document.getElementById('progress');

    // ---- tiny helpers ---------------------------------------------------------
    function h(tag, attrs, kids) {
        const el = document.createElement(tag);
        Object.keys(attrs || {}).forEach(k => {
            if (k === 'class') el.className = attrs[k];
            else if (k === 'text') el.textContent = attrs[k];
            else if (k.indexOf('on') === 0) el.addEventListener(k.slice(2), attrs[k]);
            else if (attrs[k] === true) el.setAttribute(k, '');
            else if (attrs[k] !== false && attrs[k] != null) el.setAttribute(k, attrs[k]);
        });
        (kids || []).forEach(c => { if (c != null) el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
        return el;
    }
    const sleep = ms => new Promise(r => setTimeout(r, ms));

    async function api(method, url, body) {
        try {
            const res = await fetch(url, {
                method,
                headers: body ? { 'Content-Type': 'application/json' } : undefined,
                body: body ? JSON.stringify(body) : undefined,
            });
            let data = null;
            try { data = await res.json(); } catch (e) { /* not JSON */ }
            return { ok: res.ok, status: res.status, data };
        } catch (e) {
            return { ok: false, status: 0, data: null, offline: true };
        }
    }

    // Remembered Wi-Fi details live in this tab's sessionStorage only (cleared
    // when the tab closes). Never localStorage, never the server.
    function loadRemembered() {
        try {
            const raw = sessionStorage.getItem(STORE_KEY);
            if (!raw) return null;
            const o = JSON.parse(raw);
            return o && typeof o.ssid === 'string' ? { ssid: o.ssid, password: o.password || '' } : null;
        } catch (e) { return null; }
    }
    function saveRemembered(c) {
        try {
            if (c) sessionStorage.setItem(STORE_KEY, JSON.stringify(c));
            else sessionStorage.removeItem(STORE_KEY);
        } catch (e) { /* storage blocked: the in-memory copy still works */ }
    }

    // ---- state ----------------------------------------------------------------
    let state = W.initial({
        os: W.osFromUserAgent(navigator.userAgent),
        hint: '',
        remembered: loadRemembered(),
    });
    let lastHint = null;            // what the computer's Wi-Fi name was at the last check
    let scanResult = null;          // {supported, networks} for the Wi-Fi step
    let typedName = '';             // survives a re-render of the Wi-Fi step
    let typedPassword = '';
    let rememberChoice = true;
    let timers = [];                // intervals owned by the current step
    let waitToken = 0;

    function stopTimers() {
        timers.forEach(clearInterval);
        timers = [];
        waitToken++;
    }

    function dispatch(ev) {
        const prev = state;
        state = W.reduce(state, ev);
        if (state === prev) return false;
        // Keep the details only once a device has really come online with them,
        // and drop them again on "start over" (the password may have been wrong).
        if (ev.type === 'found') saveRemembered(state.creds);
        else if (ev.type === 'restart') saveRemembered(null);
        if (state.step !== prev.step || ev.type === 'confirmId' || ev.type === 'pick'
            || ev.type === 'probe' || ev.type === 'changeWifi' || ev.type === 'send'
            || ev.type === 'sendFail' || ev.type === 'restart' || ev.type === 'nextDevice') {
            render();
        }
        return true;
    }

    // ---- rendering ------------------------------------------------------------
    function drawProgress() {
        const at = { prepare: 0, kind: 1, join: 2, wifi: 3, sending: 3, wait: 4, done: 4 }[state.step];
        Array.prototype.forEach.call(progress.children, (li, i) => {
            li.className = i < at ? 'past' : i === at ? 'on' : '';
        });
    }

    function render() {
        stopTimers();
        card.textContent = '';
        drawProgress();
        const view = { prepare, kind, join, wifi, sending, wait, done }[state.step];
        view();
        window.scrollTo(0, 0);
    }

    function banner() {
        const out = [];
        if (state.notice) out.push(h('div', { class: 'notice', text: state.notice }));
        if (state.error) out.push(h('div', { class: 'error', role: 'alert', text: state.error.message }));
        return out;
    }

    function backButton() {
        return h('button', { text: T.back, onclick: () => dispatch({ type: 'back' }) });
    }

    // 1. Get ready
    function prepare() {
        card.appendChild(h('h2', { text: T.prepareTitle }));
        card.appendChild(h('p', { class: 'dim', text: T.prepareIntro }));
        card.appendChild(h('ul', { class: 'checks' }, T.prepareChecks.map(t => h('li', { text: t }))));
        card.appendChild(h('details', {}, [
            h('summary', { text: T.hotspotSummary }),
            h('ul', { class: 'checks small' }, T.hotspotBody.map(t => h('li', { text: t }))),
        ]));
        const note = h('p', { class: 'dim small', id: 'wifi-note' });
        card.appendChild(note);
        showHintNote(note);
        card.appendChild(h('div', { class: 'row' }, [
            h('button', { class: 'primary', text: T.prepareReady, onclick: () => dispatch({ type: 'next' }) }),
        ]));
        refreshHint().then(() => showHintNote(note));
    }

    function showHintNote(el) {
        el.textContent = state.hint ? T.prepareNoteWifi(state.hint) : '';
    }

    // The computer's own Wi-Fi name, best effort. Remembered as the default for
    // step 4 unless it is a device network (then we keep the earlier one).
    async function refreshHint() {
        const r = await api('GET', '/api/setup/wifi-hint');
        lastHint = r.ok && r.data ? (r.data.ssid || null) : null;
        const s = W.suggestion(lastHint);
        if (s && s !== state.hint) state = Object.assign({}, state, { hint: s });
        return lastHint;
    }

    // 2. Choose
    function kind() {
        card.appendChild(h('h2', { text: T.kindTitle }));
        card.appendChild(h('p', { class: 'dim', text: T.kindIntro }));
        card.appendChild(h('div', { class: 'kinds' }, ['band', 'labeler'].map(k => {
            const K = T.kinds[k];
            return h('button', { class: 'kind', onclick: () => dispatch({ type: 'pick', kind: k }) }, [
                h('div', { class: 'icon', 'aria-hidden': 'true', text: k === 'band' ? '▭' : '☝' }),
                h('strong', { text: K.title }),
                h('span', { text: K.look }),
                h('span', { text: K.reset }),
            ]);
        })));
        card.appendChild(h('div', { class: 'row' }, [backButton()]));
    }

    // 3. Join the device's own Wi-Fi
    function join() {
        banner().forEach(b => card.appendChild(b));
        card.appendChild(h('h2', { text: T.joinTitle }));
        card.appendChild(h('p', { class: 'dim', text: T.joinIntro }));
        if (state.kind === 'band') card.appendChild(h('p', { class: 'dim', text: T.joinUsbBand }));
        card.appendChild(h('ol', { class: 'how' }, T.joinOs[state.os].map(t => h('li', { text: t }))));
        card.appendChild(h('p', { class: 'dim small', text: T.joinBrief }));

        const d = state.device;
        const tick = h('span', { class: 'tick' + (d ? ' on' : ' spin') });
        const line = h('div', {}, [h('div', { text: d ? T.joinFound(W.deviceTitle(d.id, d.dev)) : T.joinWaiting })]);
        card.appendChild(h('div', { class: 'status' + (d ? ' ok' : '') }, [tick, line]));
        if (d) {
            line.appendChild(h('div', { class: 'dim', text: T.joinConfirm(W.idTail(d.id)) }));
            card.appendChild(h('div', { class: 'row' }, [
                h('button', { class: 'primary', text: T.joinYes, onclick: () => dispatch({ type: 'confirmId', yes: true }) }),
                h('button', { text: T.joinNo, onclick: () => dispatch({ type: 'confirmId', yes: false }) }),
            ]));
        }
        const hintLine = h('p', { class: 'dim small', id: 'hint-line' });
        card.appendChild(hintLine);
        showJoinHint(hintLine);
        card.appendChild(h('div', { class: 'row' }, [backButton()]));

        const poll = async () => {
            const r = await api('GET', '/api/setup/probe');
            if (state.step !== 'join') return;
            if (r.offline) { hintLine.textContent = T.errOffline; return; }
            if (r.status === 403) { hintLine.textContent = T.errLocalOnly; return; }
            if (r.ok && r.data) dispatch({ type: 'probe', result: r.data });
            if (state.step !== 'join') return;
            await refreshHint();
            showJoinHint(hintLine);
        };
        poll();
        timers.push(setInterval(poll, 2000));
    }

    function showJoinHint(el) {
        el.textContent = lastHint ? T.joinWifiNow(lastHint) : '';
    }

    // 4. Your Wi-Fi
    function wifi() {
        banner().forEach(b => card.appendChild(b));
        card.appendChild(h('h2', { text: T.wifiTitle }));
        card.appendChild(h('p', { class: 'dim', text: T.wifiIntro }));

        if (state.useSaved && state.creds) {
            card.appendChild(h('p', { text: T.wifiSaved(state.creds.ssid) }));
            card.appendChild(h('div', { class: 'row' }, [
                h('button', { class: 'primary', text: T.wifiSavedSend, onclick: () => send(null) }),
                h('button', { text: T.wifiChange, onclick: () => dispatch({ type: 'changeWifi' }) }),
                backButton(),
            ]));
            return;
        }

        const nameInput = h('input', { type: 'text', id: 'wifi-name', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false' });
        nameInput.value = typedName || (state.creds && state.creds.ssid) || state.hint || '';
        const select = h('select', { id: 'wifi-select', 'aria-label': T.wifiName });
        select.style.display = 'none';
        const scanNote = h('p', { class: 'help', text: T.wifiScanning });

        const pw = h('input', { type: 'password', id: 'wifi-password', autocomplete: 'off', spellcheck: 'false' });
        pw.value = typedPassword;
        const toggle = h('button', { type: 'button', text: T.wifiShow, onclick: () => {
            const show = pw.type === 'password';
            pw.type = show ? 'text' : 'password';
            toggle.textContent = show ? T.wifiHide : T.wifiShow;
        } });
        const rememberBox = h('input', { type: 'checkbox', id: 'wifi-remember' });
        rememberBox.checked = rememberChoice;

        card.appendChild(h('label', { for: 'wifi-name', text: T.wifiName }));
        card.appendChild(h('div', { class: 'help', text: T.wifiNameHelp }));
        card.appendChild(select);
        card.appendChild(nameInput);
        card.appendChild(scanNote);
        card.appendChild(h('label', { for: 'wifi-password', text: T.wifiPassword }));
        card.appendChild(h('div', { class: 'help', text: T.wifiPasswordHelp }));
        card.appendChild(h('div', { class: 'pw' }, [pw, toggle]));
        card.appendChild(h('label', { class: 'check', for: 'wifi-remember' }, [rememberBox, h('span', { text: T.wifiRemember })]));
        card.appendChild(h('div', { class: 'row' }, [
            h('button', { class: 'primary', id: 'wifi-send', text: T.wifiSend, onclick: () => {
                typedName = nameInput.value;
                typedPassword = pw.value;
                rememberChoice = rememberBox.checked;
                send({ ssid: nameInput.value, password: pw.value, remember: rememberBox.checked });
            } }),
            backButton(),
        ]));

        // Dropdown from the device's own scan when it has one; the text box stays
        // as the fallback and as "another network".
        const OTHER = '\u0000other';
        function fillSelect(res) {
            scanResult = res;
            const opts = res && res.supported ? W.networkOptions(res.networks) : [];
            if (!opts.length) {
                scanNote.textContent = T.wifiNoNames;
                return;
            }
            select.textContent = '';
            opts.forEach(o => select.appendChild(h('option', { value: o.name, text: o.name + (o.strength ? ' (' + o.strength + ')' : '') })));
            select.appendChild(h('option', { value: OTHER, text: T.wifiOther }));
            const cur = nameInput.value;
            select.value = opts.some(o => o.name === cur) ? cur : (cur ? OTHER : opts[0].name);
            if (select.value !== OTHER && !cur) nameInput.value = select.value;
            sync();
            select.style.display = '';
            scanNote.textContent = T.wifiOnly24;
        }
        function sync() {
            if (select.value === OTHER) { nameInput.style.display = ''; }
            else { nameInput.value = select.value; nameInput.style.display = 'none'; }
        }
        select.addEventListener('change', () => { sync(); if (select.value === OTHER) nameInput.focus(); });

        if (scanResult) fillSelect(scanResult);
        else api('GET', '/api/setup/scan').then(r => {
            if (state.step !== 'wifi' || !select.isConnected) return;
            fillSelect(r.ok ? r.data : { supported: false, networks: [] });
        });
    }

    async function send(form) {
        if (form) {
            if (!dispatch({ type: 'send', ssid: form.ssid, password: form.password, remember: form.remember })) return;
        } else if (!dispatch({ type: 'send', ssid: '', password: '', remember: true })) {
            return;
        }
        if (state.step !== 'sending') return;      // validation said no; error is shown
        const body = {
            ssid: state.sentSsid,
            password: form ? form.password : state.creds.password,
            expect_id: state.device ? state.device.id : undefined,
        };
        const r = await api('POST', '/api/setup/provision', body);
        body.password = '';
        if (r.offline) { dispatch({ type: 'sendFail', code: 'offline', message: T.errOffline }); return; }
        const d = r.data || {};
        if (r.ok && d.ok) {
            typedPassword = '';
            dispatch({ type: 'sendOk', id: d.id, since: d.since, now: Date.now() });
        } else {
            dispatch({ type: 'sendFail', code: d.code, message: d.message });
        }
    }

    // sending: a moment between the click and the device's answer
    function sending() {
        card.appendChild(h('div', { class: 'status' }, [h('span', { class: 'tick spin' }), h('div', { text: T.sending })]));
    }

    // 5. Waiting for the device
    function wait() {
        const name = state.sentSsid;
        card.appendChild(h('p', { class: 'big', text: name ? T.waitSaved(name) : T.waitSavedNoName }));
        const list = h('div', {});
        card.appendChild(list);
        card.appendChild(h('p', { class: 'dim small', text: T.waitKeepOpen }));
        const troubleBox = h('div', {});
        card.appendChild(troubleBox);

        const signals = { probeAnswering: null, hintSsid: null, found: false };
        let troubleShown = false;
        let lastScanS = null;
        const token = ++waitToken;

        function paint() {
            const v = W.waitView(state, signals, Date.now());
            list.textContent = '';
            v.items.forEach(it => list.appendChild(h('div', { class: 'status' + (it.done ? ' ok' : '') }, [
                h('span', { class: 'tick' + (it.done ? ' on' : ' spin') }),
                h('div', { text: it.text }),
            ])));
            if (v.timedOut && !troubleShown) { troubleShown = true; drawTrouble(troubleBox); }
            return v;
        }
        paint();

        // Slow signals: is the device's setup network still answering, and which
        // Wi-Fi is this computer on now?
        async function sample() {
            if (token !== waitToken) return;
            const [p, hint] = await Promise.all([api('GET', '/api/setup/probe'), api('GET', '/api/setup/wifi-hint')]);
            if (token !== waitToken) return;
            if (p.ok && p.data) signals.probeAnswering = !!p.data.reachable;
            signals.hintSsid = hint.ok && hint.data ? (hint.data.ssid || null) : null;
            const v = paint();
            if (W.shouldScanNow(v.elapsedS, lastScanS)) {
                lastScanS = v.elapsedS;
                api('POST', '/api/discovery/scan', {});     // actively look; the wait below sees the result
            }
        }
        sample();
        timers.push(setInterval(sample, 2500));
        timers.push(setInterval(paint, 1000));

        // Fast signal: the device announcing itself on the user's network.
        (async function loop() {
            while (token === waitToken && state.step === 'wait') {
                const q = new URLSearchParams({ device_id: state.sentId, timeout: '3' });
                if (state.since != null) q.set('since', String(state.since));
                const r = await api('GET', '/api/setup/wait?' + q.toString());
                if (token !== waitToken) return;
                if (r.ok && r.data && r.data.found) {
                    const dev = r.data.device || {};
                    signals.found = true;
                    dispatch({ type: 'found', id: dev.device_id, ip: dev.ip, dev: dev.device_type });
                    return;
                }
                if (!r.ok) await sleep(1500);
            }
        })();
    }

    function drawTrouble(box) {
        box.textContent = '';
        const wrap = h('div', { class: 'trouble' });
        wrap.appendChild(h('h3', { text: T.troubleTitle }));
        wrap.appendChild(h('p', { class: 'dim', text: T.troubleIntro }));
        const result = h('div', { class: 'small' });
        const items = W.troubleshootingSteps(state.os).map(s => {
            const li = h('li', {}, [h('strong', { text: s.title }), h('span', { text: s.body })]);
            if (s.action === 'restart') {
                li.appendChild(h('div', {}, [h('button', { class: 'link', text: T.troubleAgain, onclick: () => dispatch({ type: 'restart' }) })]));
            }
            if (s.action === 'check') li.appendChild(checkButtons(result));
            if (s.action === 'address') li.appendChild(addressForm(result));
            return li;
        });
        wrap.appendChild(h('ol', {}, items));
        wrap.appendChild(result);
        wrap.appendChild(h('p', { class: 'dim small', text: T.doneForget }));
        box.appendChild(wrap);
    }

    function checkButtons(result) {
        const run = h('button', { type: 'button', text: T.troubleCheck });
        const find = h('button', { type: 'button', text: T.troubleFind });
        run.addEventListener('click', async () => {
            run.disabled = true; run.textContent = T.troubleCheckBusy;
            const r = await api('GET', '/api/discovery/diagnose');
            run.disabled = false; run.textContent = T.troubleCheck;
            result.textContent = '';
            const d = r.ok ? W.describeDiagnose(r.data) : null;
            if (!d) { result.appendChild(h('p', { text: T.troubleCheckNone })); return; }
            if (d.summary) result.appendChild(h('p', { text: d.summary }));
            if (d.lines.length) result.appendChild(h('ul', {}, d.lines.map(l => h('li', { text: l }))));
        });
        find.addEventListener('click', async () => {
            find.disabled = true; find.textContent = T.troubleFindBusy;
            await api('POST', '/api/discovery/scan', {});
            find.disabled = false; find.textContent = T.troubleFind;
        });
        return h('div', { class: 'row' }, [run, find]);
    }

    function addressForm(result) {
        const input = h('input', { type: 'text', 'aria-label': T.troubleAddressLabel, placeholder: '192.168.1.23', autocomplete: 'off' });
        const go = h('button', { type: 'button', text: T.troubleAddressGo });
        go.addEventListener('click', async () => {
            const addr = input.value.trim();
            if (!addr) return;
            result.textContent = T.troubleAddressBusy + addr + '...';
            const r = await api('POST', '/api/discovery/probe', { ip: addr });
            if (r.ok && r.data && r.data.device_id) {
                if (!state.sentId || r.data.device_id === state.sentId) {
                    dispatch({ type: 'found', id: r.data.device_id, ip: r.data.ip, dev: r.data.device_type });
                    return;
                }
            }
            result.textContent = T.troubleAddressNone(addr);
        });
        return h('div', { class: 'row' }, [input, go]);
    }

    // 6. Done
    function done() {
        const f = state.found;
        card.appendChild(h('div', { class: 'success' }, [
            h('div', { class: 'tick on mark', 'aria-hidden': 'true' }),
            h('div', { class: 'big', text: T.doneOnline(W.deviceTitle(f.id, f.dev), f.ip || '?') }),
        ]));
        card.appendChild(h('p', { class: 'dim small', text: T.doneForget }));
        card.appendChild(h('div', { class: 'row' }, [
            h('button', { class: 'primary', text: T.doneNext, onclick: () => dispatch({ type: 'nextDevice' }) }),
            h('a', { class: 'btn', href: '/', text: T.doneFinish }),
        ]));
    }

    // Start. The first step needs the computer's Wi-Fi name for its note, but the
    // page must work even if that lookup fails (or the page loaded offline).
    render();
})();

// Record guidance: pure functions that decide what the one Record button will
// do and, when it cannot do anything yet, say why in plain words.
//
// Kept free of the DOM so the wording and the logic are unit tested under Node
// (pc/tests/js/guide.test.js). Plain script (NOT an ES module) exposing
// window.OMGuide, like heatmap-color.js: app.js loads as a plain <script> tag.
// Under Node the same file is require()-able via module.exports.
(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.OMGuide = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const DEVICE_FRESH_S = 2.0;   // same staleness threshold as the device list

    function deviceFresh(d) {
        return d.last_seen_age == null || d.last_seen_age < DEVICE_FRESH_S;
    }

    // Label source <select> values (unchanged: saved prefs + server contract).
    const AUTO_SOURCES = ['lask5', 'webcam', 'vr', 'gamepad'];
    const SOURCE_DEVICE_TYPE = {
        lask5: 'lask5', webcam: 'webcam_hand', vr: 'quest_hand', gamepad: 'gamepad',
    };
    // Priority when several labelers stream at once.
    const AUTO_PRIORITY = ['lask5', 'webcam', 'vr', 'gamepad'];

    function sourceStreaming(devices, source) {
        const type = SOURCE_DEVICE_TYPE[source];
        return !!type && devices.some(d => d.device_type === type && deviceFresh(d));
    }

    // Which Labels option to show until the user picks one themselves.
    //  - userChose: a manual pick is never overridden.
    //  - the current pick stays while its device is streaming (no flip-flop
    //    when a second labeler shows up later);
    //  - otherwise the first streaming of lask5, webcam, headset, gamepad;
    //  - with nothing streaming, keep an auto-able current pick, else LASK5
    //    (the kit labeler).
    function autoLabelSource(devices, current, userChose) {
        if (userChose) return current;
        const devs = devices || [];
        if (AUTO_SOURCES.includes(current) && sourceStreaming(devs, current)) return current;
        const first = AUTO_PRIORITY.find(s => sourceStreaming(devs, s));
        if (first) return first;
        return AUTO_SOURCES.includes(current) ? current : 'lask5';
    }

    // ---- plain reasons (exact wording is tested) ----
    const R = {
        noBand: 'No band found yet. Turn your band on and wait about 10 seconds. '
            + 'See the Getting started panel if it does not appear.',
        staleBand: 'A band has stopped sending data. Check it is on and charged.',
        tagArms: 'Tell me which arm each band is on. '
            + 'Use the Left arm / Right arm buttons on each band.',
        sameArm: (side, other) => `Both bands are set to the same arm (${side}). `
            + `Set one of them to the ${other}.`,
        oneUntagged: id => `${id} has no arm set. Set it to Left arm or Right arm, `
            + 'or unplug it if you do not want to record it.',
        noHeadset: 'The headset is not sending hand data. Put on the Quest and open the '
            + 'OpenMuscle page, or change "Finger data from" to another labeler.',
        noHeadsetBoth: 'Both of your hands need to be visible to the headset. '
            + 'Open the OpenMuscle page on the Quest with both hands selected.',
        noLask: 'The finger labeler (LASK5) is not sending data. '
            + 'Check it is turned on and on the same Wi-Fi as this computer.',
        noWebcam: 'The camera is not on. Open the Camera panel on this page, '
            + 'press Start camera and hold your hand up.',
        staleWebcam: 'The camera has stopped sending hand data. Keep the camera window '
            + 'visible next to this one (side by side, not behind it), or use the Camera '
            + 'panel on this page, which keeps tracking while you record.',
        noPad: 'The game controller is not sending data. Plug it in and press any button.',
    };

    function armWord(role) { return role === 'left' ? 'left arm' : role === 'right' ? 'right arm' : null; }

    function bandsText(bands) {
        if (bands.length === 1) {
            const w = armWord(bands[0].role);
            return w ? `1 band (${w})` : '1 band';
        }
        return `${bands.length} bands (${bands.map(b => armWord(b.role) || 'no arm').join(', ')})`;
    }

    const LABELER_TEXT = {
        vr: 'headset hand data', lask5: 'finger labeler', webcam: 'webcam hand data',
        gamepad: 'game controller',
    };

    // Human "Ready" sentence for a ready plan. `bands` are the recorded bands.
    function readyText(source, bands) {
        const b = bandsText(bands);
        if (source === 'none') return `Ready: ${b}, no finger data (cannot be used to train). Press Record.`;
        return `Ready: ${b} + ${LABELER_TEXT[source] || 'finger data'}. Press Record.`;
    }

    // ctx: { devices, source, windowMs (number|null), sensorId, labelId,
//        mirrored (optional: the active session is mirrored) }
    // Returns { ready, mode, reason?, text, summary?, endpoint?, body? }.
    // `text` is the plain line shown to the operator; `summary` is the
    // technical string (tooltip only).
    function deriveRecordPlan(ctx) {
        const devices = ctx.devices || [];
        const winMs = Number.isFinite(ctx.windowMs) ? ctx.windowMs : null;
        // Server defaults when the advanced window box is empty: lask5=100,
        // quest_hand=175 (DEFAULT_WINDOW_MS_BY_TYPE in state.py).
        const winText = def => `win ${winMs ?? def}ms`;
        const blocked = reason => ({ ready: false, mode: 'blocked', reason, text: reason });
        const ready = (plan, bands) => Object.assign(plan, {
            ready: true, text: readyText(ctx.source || 'vr', bands),
        });

        // Advanced override: an explicit device pick forces the legacy
        // single-source path (today's exact behavior).
        const sensorVal = ctx.sensorId || '';
        const labelVal = ctx.labelId || '';
        if (sensorVal || labelVal) {
            const body = { filename: null };
            if (sensorVal) body.sensor_device_id = sensorVal;
            if (labelVal === '__none__') body.label_device_id = '';
            else if (labelVal)           body.label_device_id = labelVal;
            return {
                ready: true, mode: 'advanced', endpoint: '/api/recording', body,
                text: 'Ready: using the devices you picked under Advanced. Press Record.',
                summary: `advanced | sensor ${sensorVal || 'auto'} | label ${
                    labelVal === '__none__' ? 'none' : (labelVal || 'auto')} | ${winText(100)}`,
            };
        }

        const source = ctx.source || 'vr';
        const flex = devices.filter(d => d.device_type === 'flexgrid');
        if (!flex.length) return blocked(R.noBand);

        // Band freshness applies to every label path. A tagged band that went
        // quiet blocks (never silently dropped); an untagged stale one is
        // ignored. Nothing fresh at all is the same message.
        const live = flex.filter(deviceFresh);
        const isArm = d => d.role === 'left' || d.role === 'right';
        if (!live.length || flex.some(d => isArm(d) && !deviceFresh(d))) {
            return blocked(R.staleBand);
        }

        const tagged = live.filter(isArm);
        const untagged = live.filter(d => !isArm(d));
        if (live.length >= 2) {
            // Every band must have a unique arm; one tagged + one untagged is
            // blocked rather than recording only the tagged band.
            if (!tagged.length) return blocked(R.tagArms);
            if (untagged.length) return blocked(R.oneUntagged(untagged[0].device_id));
            for (const side of ['left', 'right']) {
                if (tagged.filter(d => d.role === side).length > 1) {
                    return blocked(R.sameArm(side, side === 'left' ? 'right arm' : 'left arm'));
                }
            }
        }
        const bandL = live.find(d => d.role === 'left');
        const bandR = live.find(d => d.role === 'right');
        const both = live.length >= 2 && bandL && bandR;
        // Lone band: tagged or not (an untagged lone band is allowed).
        const soloBand = live.length === 1 ? live[0] : null;
        const recorded = both ? [bandL, bandR] : [soloBand];

        const quests = devices.filter(d => d.device_type === 'quest_hand');
        // Same side test as renderHandViewer (quest-left / quest-right ids).
        const questSide = side => quests.find(d =>
            String(d.device_id).toLowerCase().includes(side));
        const lask = devices.find(d => d.device_type === 'lask5' && deviceFresh(d));

        if (source === 'vr') {
            if (both) {
                const missing = ['left', 'right'].filter(s => {
                    const q = questSide(s);
                    return !(q && deviceFresh(q));
                });
                if (missing.length) return blocked(R.noHeadsetBoth);
                return ready({
                    mode: '2h-vr',
                    endpoint: '/api/recording/bilateral',
                    body: { filename: null },
                    summary: `2H | L ${bandL.device_id} + R ${bandR.device_id} | labels: VR both hands | ${winText(175)}`,
                }, recorded);
            }
            if (!quests.some(deviceFresh)) return blocked(R.noHeadset);
            const role = soloBand.role || 'left';
            return ready({
                mode: '1h-vr',
                endpoint: '/api/recording',
                // label_device_id omitted: server auto-pick prefers quest_hand
                body: { filename: null, sensor_device_id: soloBand.device_id, role },
                summary: `1H | ${soloBand.device_id} (${role}) | labels: VR ${role} hand | ${winText(175)}`,
            }, recorded);
        }

        if (source === 'lask5') {
            if (!lask) return blocked(R.noLask);
            if (both) {
                return ready({
                    mode: '2band-lask',
                    endpoint: '/api/recording/multiband',
                    body: { filename: null },
                    summary: `2 bands | labels: LASK5 ${lask.device_id} | ${winText(100)}`,
                }, recorded);
            }
            // Omit both device ids for a lone untagged band = server auto-pick.
            const body = { filename: null };
            if (soloBand.role) {
                body.sensor_device_id = soloBand.device_id;
                body.role = soloBand.role;
            }
            return ready({
                mode: '1h-lask',
                endpoint: '/api/recording', body,
                summary: `1H | ${soloBand.device_id}${soloBand.role ? ` (${soloBand.role})` : ''} | labels: LASK5 ${lask.device_id} | ${winText(100)}`,
            }, recorded);
        }

        if (source === 'webcam') {
            // The /webcam page tracks a hand with the computer's camera and
            // streams it to /ws/webcam, so it shows up as a device_type=
            // "webcam_hand" label device (ids webcam-left / webcam-right). One
            // hand labels every recorded band: with a band on each arm and one
            // hand in view, both bands get that hand's labels, which is what
            // mirrored training wants.
            const allCams = devices.filter(d => d.device_type === 'webcam_hand');
            const cams = allCams.filter(deviceFresh);
            if (!cams.length) return blocked(allCams.length ? R.staleWebcam : R.noWebcam);
            // Only fresh hands are candidates (a hand from a page that was
            // closed or hidden earlier must never be picked). With a mirrored
            // session one hand labels both bands, so take the one streaming
            // fastest; otherwise prefer the hand on the band's own side.
            const fastest = () => cams.reduce((a, b) => ((b.hz || 0) > (a.hz || 0) ? b : a));
            const camFor = side => (ctx.mirrored ? null : cams.find(d =>
                String(d.device_id).toLowerCase().includes(side))) || fastest();
            if (both) {
                const cam = fastest();
                return ready({
                    mode: '2band-webcam',
                    endpoint: '/api/recording',
                    body: { filename: null, label_device_id: cam.device_id,
                            sensor_device_id: bandL.device_id, role: 'left',
                            extra_sensors: [{ device_id: bandR.device_id, role: 'right' }] },
                    summary: `2 bands | L ${bandL.device_id} + R ${bandR.device_id} | labels: webcam ${cam.device_id} | ${winText(175)}`,
                }, recorded);
            }
            // Pin the webcam hand as the label so auto-pick can't prefer a
            // stray quest/lask5. Prefer the hand on the band's own side.
            const cam = camFor(soloBand.role || 'right');
            const body = { filename: null, label_device_id: cam.device_id };
            if (soloBand.role) { body.sensor_device_id = soloBand.device_id; body.role = soloBand.role; }
            return ready({
                mode: '1h-webcam',
                endpoint: '/api/recording', body,
                summary: `1H | ${soloBand.device_id}${soloBand.role ? ` (${soloBand.role})` : ''} | labels: webcam ${cam.device_id} | ${winText(175)}`,
            }, recorded);
        }

        if (source === 'gamepad') {
            // The dashboard browser streams the controller to /ws/gamepad, so
            // it shows up as a device_type="gamepad" label device (gamepad.js).
            const pad = devices.find(d => d.device_type === 'gamepad' && deviceFresh(d));
            if (!pad) return blocked(R.noPad);
            if (both) {
                // multiband auto-picks the label device; gamepad is in the
                // server's AUTO_LABEL_TYPE_PREFERENCE so a lone controller is found.
                return ready({
                    mode: '2band-gamepad',
                    endpoint: '/api/recording/multiband',
                    body: { filename: null },
                    summary: `2 bands | labels: gamepad ${pad.device_id} | ${winText(120)}`,
                }, recorded);
            }
            // Pin the gamepad as the label so auto-pick can't prefer a stray quest/lask.
            const body = { filename: null, label_device_id: pad.device_id };
            if (soloBand.role) { body.sensor_device_id = soloBand.device_id; body.role = soloBand.role; }
            return ready({
                mode: '1h-gamepad',
                endpoint: '/api/recording', body,
                summary: `1H | ${soloBand.device_id}${soloBand.role ? ` (${soloBand.role})` : ''} | labels: gamepad ${pad.device_id} | ${winText(120)}`,
            }, recorded);
        }

        // source === 'none': sensor-only (label_device_id "" disables pairing)
        if (both) {
            return ready({
                mode: 'sensor-2band',
                endpoint: '/api/recording',
                body: { filename: null, label_device_id: '', sensor_device_id: bandL.device_id,
                        role: 'left', extra_sensors: [{ device_id: bandR.device_id, role: 'right' }] },
                summary: `2 bands | L ${bandL.device_id} + R ${bandR.device_id} | labels: none (sensor only) | ${winText(100)}`,
            }, recorded);
        }
        const role = soloBand.role || 'left';
        return ready({
            mode: 'sensor-only',
            endpoint: '/api/recording',
            body: { filename: null, label_device_id: '', sensor_device_id: soloBand.device_id, role },
            summary: `1 band | ${soloBand.device_id} (${role}) | labels: none (sensor only) | ${winText(100)}`,
        }, recorded);
    }

    return { DEVICE_FRESH_S, deviceFresh, autoLabelSource, deriveRecordPlan, readyText, REASONS: R };
});

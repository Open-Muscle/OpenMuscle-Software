// Device setup wizard: the pure part. Every word the page shows, the step
// machine, the checklist logic and the troubleshooting order live here so they
// can be unit tested under Node (pc/tests/js/wizard.test.js) without a DOM.
//
// Plain script (NOT an ES module) exposing window.OMWizard, like guide.js. Under
// Node the same file is require()-able via module.exports.
//
// Wording rule: this page is for someone who has never opened a terminal. The
// words V4, probe, UDP, subnet and SSID never appear in anything the user reads;
// say "device", "Wi-Fi name" and "address". A test enforces it.
(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.OMWizard = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const WAIT_TIMEOUT_S = 60;        // after this the troubleshooting list opens
    const RESTART_GUESS_S = 25;       // a restart takes about 20 s; assume done after this
    const USB_RESTART_GUESS_S = 5;    // over USB the band restarts about a second after it saves
    const SETUP_PREFIX = 'OM-';       // every device in setup mode names its network OM-...

    // ---- all user-visible wording ---------------------------------------------
    const TEXT = {
        pageTitle: 'Set up a device',
        back: 'Back',
        toDashboard: 'Back to the live view',
        steps: ['Get ready', 'Choose', 'Join', 'Your Wi-Fi', 'Finish'],

        prepareTitle: 'Before you start',
        prepareIntro: 'This takes a few minutes per device. Check these first:',
        prepareChecks: [
            'Your Wi-Fi is a 2.4 GHz network. Most home routers offer one. A network that is only 5 GHz will not work, because the device cannot join it.',
            'You know the name and password of that Wi-Fi.',
            'It does not ask you to sign in on a web page (hotels, schools, hospitals and offices often do), and it does not need a work or school login.',
        ],
        hotspotSummary: 'No suitable Wi-Fi? Use your phone as a hotspot',
        hotspotBody: [
            'iPhone: open Settings, then Personal Hotspot, and turn on Allow Others to Join. Also turn on Maximize Compatibility so it uses 2.4 GHz.',
            'Android: open Settings, then Hotspot, and choose the 2.4 GHz band.',
            'Keep the hotspot switched on until every device is set up. Your computer must be on the same hotspot afterwards.',
        ],
        prepareNoteWifi: name => 'This computer is on "' + name + '" right now. We will offer it as the default later.',
        prepareReady: 'I am ready',

        kindTitle: 'What are you setting up?',
        kindIntro: 'Turn the device on. For a band, the easiest way is the USB cable: plug the band into this computer and the Wi-Fi details go down the cable, so there is no setup network to join. A labeler is set up through its own Wi-Fi network instead.',
        kinds: {
            band: {
                title: 'A band',
                look: 'Plug it into this computer with its USB cable. You do not need to join any Wi-Fi network.',
                reset: 'No cable that carries data? The next step lets you use the band\'s own Wi-Fi instead.',
            },
            labeler: {
                title: 'A finger labeler (optional)',
                look: 'The screen says WIFI SETUP and the light is blinking.',
                reset: 'Not showing that? Open the menu and choose Reset Wi-Fi.',
            },
        },

        joinTitle: 'Join the device\'s own Wi-Fi',
        joinIntro: 'The device makes a small Wi-Fi network of its own while it is being set up. Connect this computer to it for a minute.',
        joinOs: {
            windows: [
                'Click the Wi-Fi icon at the bottom right of the screen.',
                'Choose the network whose name starts with OM-.',
                'Type the Setup password from the sticker on the device (on a device with a screen, the part after PW), then connect.',
            ],
            mac: [
                'Click the Wi-Fi icon in the bar at the top of the screen.',
                'Choose the network whose name starts with OM-.',
                'Type the Setup password from the sticker on the device (on a device with a screen, the part after PW), then join.',
            ],
            linux: [
                'Open the network menu from the top or bottom bar and choose Wi-Fi.',
                'Choose the network whose name starts with OM-.',
                'Type the Setup password from the sticker on the device (on a device with a screen, the part after PW), then connect.',
            ],
        },
        joinUsbBand: 'Keep the band plugged into its USB cable during setup. On battery alone it cannot start its setup network.',
        joinBandLook: 'With the USB cable plugged in, the light blinks blue, two quick blinks then a pause, over a faint glow. That is normal for a band without a screen. (A band with a screen says WIFI SETUP.) The kit bands have no screen: their name and password are on the sticker on the band.',
        joinBandReset: 'Only the faint glow, and no blue blink? Check the USB cable is plugged in. A dark light means the band is switched off: press the power button once. Otherwise hold MENU and SELECT together for 3 seconds. The light then goes back to blinking blue.',
        joinBrief: 'This computer will briefly leave your usual Wi-Fi, so web pages may stop loading for a few minutes. This page keeps working. We bring you back at the end.',
        joinWaiting: 'Looking for the device. This ticks green as soon as your computer is connected to it.',
        joinFound: title => 'Found ' + title,
        joinConfirm: tail => 'Does the name end with ' + tail + '? Compare with the Setup Wi-Fi line on the sticker (or the device screen).',
        joinYes: 'Yes, that is the one',
        joinNo: 'No, it is different',
        joinWrongOne: 'That is not the device you wanted. Turn off other devices in setup mode, or move them away, so only the one you are setting up is on. Then join its network again, or press Back to start this step over.',
        joinWifiNow: name => 'This computer is connected to "' + name + '".',
        joinKindFixed: kind => 'You picked a different kind of device, but this is ' + kind + '. We carried on with the one we found.',
        joinAlreadyOnline: 'This device is already on a Wi-Fi network, so it will not take new Wi-Fi details. Reset its Wi-Fi from its menu (hold MENU and SELECT for 3 seconds on a band), then try again.',

        wifiTitle: 'Choose your Wi-Fi',
        wifiIntro: 'These details go to the device so it can join your Wi-Fi. They are kept in this browser tab only, never saved on disk.',
        wifiName: 'Wi-Fi name',
        wifiNameHelp: 'Pick it from the list, or type it exactly as it appears, including capital letters and spaces.',
        wifiOther: 'Another network (type it)',
        wifiScanning: 'Looking for networks the device can see...',
        wifiPassword: 'Wi-Fi password',
        wifiPasswordHelp: 'Leave it empty only if your Wi-Fi has no password.',
        wifiShow: 'Show',
        wifiHide: 'Hide',
        wifiRemember: 'Remember for the next device (this browser tab only)',
        wifiSend: 'Send to the device',
        wifiSaved: name => 'Use the same Wi-Fi as before: "' + name + '"',
        wifiSavedSend: 'Send the same Wi-Fi details',
        wifiChange: 'Use a different Wi-Fi',
        wifiNoNames: 'The device did not list networks, so type the name yourself.',
        wifiOnly24: 'Only 2.4 GHz networks work, so only those are listed.',

        // The USB cable path: the Wi-Fi details go down the band's cable.
        usbTitle: 'Plug the band into this computer',
        usbIntro: 'Use the band\'s USB cable and plug the other end into this computer, not into a wall charger. The cable must carry data: some cables only charge. Keep it plugged in until setup is finished.',
        usbWaiting: 'Looking for a band on the USB cable...',
        usbNone: 'No band found yet. Check the cable is pushed in at both ends. If it still does not show up, try another cable (some only charge) or another USB port on this computer.',
        usbSilent: 'A band is plugged in but is not answering. Unplug the cable and plug it back in (or press the BOOT button), then try again.',
        usbFound: title => 'Found ' + title,
        usbCheck: tail => 'Does the Setup Wi-Fi line on its sticker end with ' + tail + '? If not, this is a different band.',
        usbChoose: 'More than one band is plugged in. Choose the one to set up now, or unplug the others.',
        usbAlready: name => 'This band is already set up for "' + name + '". Sending new details replaces them.',
        usbPickFirst: 'Plug in a band and wait for it to be found first.',
        usbNameHelp: 'Type it exactly as it appears, including capital letters and spaces. Only 2.4 GHz networks work.',
        usbOwn: 'Use the band\'s own Wi-Fi instead',
        usbOwnHelp: 'No cable that carries data? The band can make a small Wi-Fi network of its own for a few minutes, and you join it from this computer.',
        roleLeft: 'left arm',
        roleRight: 'right arm',
        roleLabeler: 'labeler',

        sending: 'Sending...',

        waitSaved: name => 'Saved. The device is restarting (about 20 seconds). Now reconnect this computer to "' + name + '".',
        waitSavedNoName: 'Saved. The device is restarting (about 20 seconds). Now reconnect this computer to your usual Wi-Fi.',
        waitRestarted: 'The device has restarted',
        waitBack: name => 'This computer is back on "' + name + '"',
        waitBackUnknown: 'This computer is back on your usual Wi-Fi',
        waitSavedUsb: name => 'Saved on the band. It is restarting (about 20 seconds) and will join "' + name + '". Keep it plugged in until it is online.',
        waitUsbSaved: 'The band has your Wi-Fi details',
        waitFound: 'The device is online',
        waitKeepOpen: 'Keep this page open. It carries on by itself.',

        doneTitle: 'All set',
        doneOnline: (title, ip) => title + ' is online at ' + ip,
        doneNext: 'Set up the next device',
        doneFinish: 'Go to the live view',
        doneForget: 'Tidy up: in this computer\'s Wi-Fi list, choose "Forget" for the OM- network, so it does not reconnect to the device by mistake.',

        troubleTitle: 'The device has not shown up yet',
        troubleIntro: 'Try these in order. This page keeps looking while you do.',
        troubleSteps: {
            password: {
                title: 'The Wi-Fi password was wrong',
                body: 'The device goes back to setup mode by itself when it cannot join (its light returns to blinking blue). If it joined a network by mistake, hold MENU and SELECT for 3 seconds. Then start again and retype the password with Show turned on.',
            },
            passwordUsb: {
                title: 'The Wi-Fi name or password was wrong',
                body: 'A band that cannot join your Wi-Fi goes back to setup mode by itself. Keep it plugged in, start again here, and retype the name and password with Show turned on.',
            },
            band: {
                title: 'The network is 5 GHz only, or asks you to sign in',
                body: 'Devices only join 2.4 GHz networks with a normal password. Try your phone as a hotspot (see Before you start), or a different network.',
            },
            network: {
                title: 'This computer is on a different network',
                body: 'The computer and the device must be on the same Wi-Fi. Check the Wi-Fi icon on this computer.',
            },
            firewall: {
                title: 'This computer\'s firewall is blocking the device',
                windows: 'Windows may have asked whether to let Python use the network. If you chose Cancel, open Windows Security, then Firewall & network protection, then Allow an app through firewall, and tick Private for Python.',
                mac: 'Open System Settings, then Privacy & Security, then Local Network, and make sure Terminal (or your Python app) is switched on. If macOS asked about incoming connections, choose Allow.',
                linux: 'If your firewall is on, allow the ports used by OpenMuscle: sudo ufw allow 3140 and sudo ufw allow 3141 (or the same two ports in firewalld).',
            },
            isolation: {
                title: 'The router keeps devices apart',
                body: 'Some routers have "client isolation", "AP isolation" or a "guest network" setting that stops devices from seeing each other. Turn it off, or use a normal (non-guest) network.',
            },
            address: {
                title: 'Add the device by its address',
                body: 'If you can find the device on your router\'s list of devices, type its address here (it looks like 192.168.1.23).',
            },
        },
        troubleCheck: 'Run a check',
        troubleCheckBusy: 'Checking...',
        troubleCheckNone: 'The detailed check is not available in this version. Follow the steps above.',
        troubleFind: 'Find my devices',
        troubleFindBusy: 'Looking...',
        troubleAddressLabel: 'Device address',
        troubleAddressGo: 'Add',
        troubleAddressBusy: 'Looking for a device at ',
        troubleAddressNone: addr => 'Nothing answered at ' + addr + '. Check the address and that the device and this computer are on the same Wi-Fi.',
        troubleAgain: 'Start over with this device',

        errReach: 'Cannot reach the device. Check that this computer is still connected to the device\'s OM- network, then try again.',
        errGeneric: 'Something went wrong. Please try again.',
        errWrongDevice: 'A different device answered. Make sure only the device you are setting up is in setup mode, then try again.',
        errHost: 'Setup only talks to a device on its own Wi-Fi network.',
        errName: 'Type the Wi-Fi name.',
        errNameLong: 'The Wi-Fi name can be at most 32 characters.',
        errPasswordShort: 'A Wi-Fi password has at least 8 characters.',
        errPasswordLong: 'A Wi-Fi password has at most 63 characters.',
        errLocalOnly: 'Setup only works in the browser on the computer that is running OpenMuscle. Open http://localhost:8000/setup on that computer.',
        errOffline: 'This page lost contact with OpenMuscle. Is the program still running?',
    };

    // ---- small helpers --------------------------------------------------------
    function idTail(id) {
        id = String(id || '');
        return id.length > 6 ? id.slice(-6) : id;
    }

    function kindOf(dev) {
        if (dev === 'flexgrid') return 'band';
        if (dev === 'lask5') return 'labeler';
        return null;
    }

    function kindName(kind) {
        return kind === 'labeler' ? 'a labeler' : kind === 'band' ? 'a band' : 'a device';
    }

    function deviceTitle(id, dev) {
        const k = kindOf(dev);
        const word = k === 'band' ? 'Band' : k === 'labeler' ? 'Labeler' : 'Device';
        return word + ' ' + id;
    }

    function roleWord(role) {
        return role === 'left' ? TEXT.roleLeft : role === 'right' ? TEXT.roleRight
            : role === 'labeler' ? TEXT.roleLabeler : '';
    }

    // One line for a band found on the USB cable: "Band flexgrid-d7af0b (left arm)".
    function usbBandLabel(b) {
        b = b || {};
        const parts = [];
        const r = roleWord(b.role);
        if (r) parts.push(r);
        return deviceTitle(b.id, 'flexgrid') + (parts.length ? ' (' + parts.join(', ') + ')' : '');
    }

    // The answering bands out of a /api/setup/usb/ports reply, and how many
    // plugged-in ports were silent.
    function usbBandsFrom(result) {
        const list = result && Array.isArray(result.ports) ? result.ports : [];
        const bands = list.filter(p => p && p.answered && p.id).map(p => ({
            port: String(p.port || ''), id: String(p.id), role: String(p.role || ''),
            state: String(p.state || ''), wifi_ssid: String(p.wifi_ssid || ''), fw: String(p.fw || ''),
        }));
        return { bands, silent: list.filter(p => p && !p.answered).length };
    }

    function osFromUserAgent(ua) {
        ua = String(ua || '');
        if (/Windows/i.test(ua)) return 'windows';
        if (/Macintosh|Mac OS X/i.test(ua)) return 'mac';
        return 'linux';
    }

    // The Wi-Fi name worth offering as a default: not empty, not a device network.
    function suggestion(hint) {
        const h = String(hint || '').trim();
        if (!h || h.indexOf(SETUP_PREFIX) === 0) return '';
        return h;
    }

    function byteLength(s) {
        if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(s).length;
        return unescape(encodeURIComponent(s)).length;
    }

    // Same limits the device enforces (bytes, not characters). Returns a plain
    // sentence, or null when the details are fine.
    function validateWifi(ssid, password) {
        ssid = String(ssid == null ? '' : ssid);
        password = String(password == null ? '' : password);
        if (!ssid) return TEXT.errName;
        if (byteLength(ssid) > 32) return TEXT.errNameLong;
        if (password) {
            const n = byteLength(password);
            if (n < 8) return TEXT.errPasswordShort;
            if (n > 63) return TEXT.errPasswordLong;
        }
        return null;
    }

    // Server error codes -> sentences (the server's own message wins when set and
    // the code is unknown).
    function errorMessage(code, message) {
        switch (code) {
            case 'unreachable': return TEXT.errReach;
            case 'wrong_device': return TEXT.errWrongDevice;
            case 'host_not_allowed': return TEXT.errHost;
            case 'not_in_setup_mode': return TEXT.joinAlreadyOnline;
            default: return message || TEXT.errGeneric;
        }
    }

    // Drop duplicate names, strongest first, hide anything without a name.
    function networkOptions(networks) {
        const seen = new Set();
        const list = (networks || []).filter(n => n && n.ssid).slice();
        list.sort((a, b) => (b.rssi == null ? -999 : b.rssi) - (a.rssi == null ? -999 : a.rssi));
        return list.filter(n => {
            if (seen.has(n.ssid)) return false;
            seen.add(n.ssid);
            return true;
        }).map(n => ({ name: n.ssid, strength: signalWord(n.rssi) }));
    }

    function signalWord(rssi) {
        if (rssi == null || isNaN(rssi)) return '';
        if (rssi >= -60) return 'strong';
        if (rssi >= -75) return 'good';
        return 'weak';
    }

    // ---- the step machine -----------------------------------------------------
    // step: prepare -> kind -> join -> wifi -> sending -> wait -> done
    // A band takes a shorter road by default: prepare -> kind -> usb -> sending
    // -> wait -> done, with the Wi-Fi details going down the USB cable. From the
    // usb step "the band's own Wi-Fi instead" leads to join -> wifi as above.
    function initial(opts) {
        opts = opts || {};
        return {
            step: opts.skipPrepare ? 'kind' : 'prepare',
            os: opts.os || 'windows',
            hint: suggestion(opts.hint),
            kind: null,
            device: null,           // {id, dev, ap_name, fw} once the device answers
            rejectedId: '',         // a device the user said is not theirs
            idConfirmed: false,
            notice: null,           // a plain sentence to show above the step
            error: null,            // {code, message} from the last failed action
            creds: opts.remembered || null,   // {ssid, password}, in memory only
            useSaved: !!opts.remembered,
            sentSsid: '',
            sentId: '',
            sentAt: null,           // Date.now() when the device accepted the details
            since: null,            // server clock at that moment (for /wait)
            found: null,            // {id, ip} when the device is online
            count: 0,               // devices finished in this visit
            via: null,              // 'usb' once the details were sent down the cable
            fromUsb: false,         // the join step was reached from the usb step
            usbBands: [],           // bands answering on USB: {port, id, role, state, wifi_ssid, fw}
            usbSilent: 0,           // plugged in but not answering
            usbPort: '',            // the chosen band's port
        };
    }

    function assign(state, patch) {
        return Object.assign({}, state, patch);
    }

    function reduce(state, ev) {
        switch (ev.type) {
            case 'next':
                if (state.step === 'prepare') return assign(state, { step: 'kind', error: null });
                return state;

            case 'back':
                if (state.step === 'kind') return assign(state, { step: 'prepare', error: null, notice: null });
                if (state.step === 'usb') return assign(state, { step: 'kind', device: null, usbBands: [], usbSilent: 0, usbPort: '', error: null, notice: null });
                if (state.step === 'join') {
                    return assign(state, { step: state.fromUsb ? 'usb' : 'kind', fromUsb: false, device: null, rejectedId: '', idConfirmed: false, error: null, notice: null });
                }
                if (state.step === 'wifi') return assign(state, { step: 'join', error: null });
                return state;

            case 'pick': {
                if (state.step !== 'kind') return state;
                // A band is set up over its USB cable unless the caller asks for its own Wi-Fi.
                const usb = ev.kind === 'band' && !ev.setupNetwork;
                return assign(state, {
                    step: usb ? 'usb' : 'join', kind: ev.kind, device: null, rejectedId: '', idConfirmed: false,
                    error: null, notice: null, via: null, fromUsb: false, usbBands: [], usbSilent: 0, usbPort: '',
                });
            }

            case 'useBandWifi':
                if (state.step !== 'usb') return state;
                return assign(state, { step: 'join', kind: 'band', fromUsb: true, device: null, rejectedId: '', idConfirmed: false, error: null, notice: null });

            case 'usbPorts': {
                if (state.step !== 'usb') return state;
                const got = usbBandsFrom(ev.result);
                let port = state.usbPort;
                if (!got.bands.some(b => b.port === port)) port = got.bands.length === 1 ? got.bands[0].port : '';
                if (port === state.usbPort && got.silent === state.usbSilent
                    && JSON.stringify(got.bands) === JSON.stringify(state.usbBands)) return state;
                return assign(state, { usbBands: got.bands, usbSilent: got.silent, usbPort: port });
            }

            case 'usbPick':
                if (state.step !== 'usb' || !state.usbBands.some(b => b.port === ev.port)) return state;
                return assign(state, { usbPort: ev.port });

            case 'usbSend': {
                if (state.step !== 'usb') return state;
                const band = state.usbBands.find(b => b.port === state.usbPort);
                if (!band) return assign(state, { error: { code: 'no_port', message: TEXT.usbPickFirst } });
                const c = state.useSaved && state.creds ? state.creds : { ssid: ev.ssid, password: ev.password };
                const problem = validateWifi(c.ssid, c.password);
                if (problem) return assign(state, { error: { code: 'invalid_request', message: problem } });
                const remember = ev.remember !== false;
                return assign(state, {
                    step: 'sending', via: 'usb', error: null, sentSsid: c.ssid,
                    device: { id: band.id, dev: 'flexgrid', ap_name: '', fw: band.fw, port: band.port },
                    creds: remember ? { ssid: c.ssid, password: c.password || '' } : null,
                    useSaved: remember,
                });
            }

            case 'probe': {
                if (state.step !== 'join') return state;
                const r = ev.result || {};
                if (!r.reachable || !r.info) {
                    // Lost it (or never had it): forget an unconfirmed match so the tick goes away.
                    return state.device && !state.idConfirmed
                        ? assign(state, { device: null, notice: null })
                        : state;
                }
                const info = r.info;
                if (info.state && info.state !== 'unprovisioned') {
                    return assign(state, { device: null, notice: TEXT.joinAlreadyOnline });
                }
                if (info.id === state.rejectedId) return state;
                if (state.device && state.device.id === info.id) return state;
                const real = kindOf(info.dev);
                const fixed = real && state.kind && real !== state.kind;
                return assign(state, {
                    device: { id: info.id, dev: info.dev, ap_name: info.ap_name || '', fw: info.fw || '' },
                    kind: real || state.kind,
                    idConfirmed: false,
                    notice: fixed ? TEXT.joinKindFixed(kindName(real)) : null,
                });
            }

            case 'confirmId':
                if (state.step !== 'join' || !state.device) return state;
                if (ev.yes) return assign(state, { step: 'wifi', idConfirmed: true, notice: null, error: null });
                return assign(state, { device: null, idConfirmed: false, rejectedId: state.device.id, notice: TEXT.joinWrongOne });

            case 'changeWifi':
                return assign(state, { useSaved: false, error: null });

            case 'send': {
                if (state.step !== 'wifi' || !state.device) return state;
                const c = state.useSaved && state.creds ? state.creds : { ssid: ev.ssid, password: ev.password };
                const problem = validateWifi(c.ssid, c.password);
                if (problem) return assign(state, { error: { code: 'invalid_request', message: problem } });
                const remember = ev.remember !== false;
                return assign(state, {
                    step: 'sending', error: null, sentSsid: c.ssid,
                    creds: remember ? { ssid: c.ssid, password: c.password || '' } : null,
                    useSaved: remember,
                });
            }

            case 'sendOk':
                if (state.step !== 'sending') return state;
                return assign(state, {
                    step: 'wait', sentId: ev.id || (state.device && state.device.id) || '',
                    sentAt: ev.now, since: ev.since == null ? null : ev.since, error: null,
                });

            case 'sendFail':
                if (state.step !== 'sending') return state;
                if (state.via === 'usb') {
                    // The server's sentence names the band that answered, which beats the generic one.
                    const msg = ev.code === 'wrong_device' && ev.message ? ev.message : errorMessage(ev.code, ev.message);
                    return assign(state, { step: 'usb', via: null, error: { code: ev.code || '', message: msg } });
                }
                return assign(state, {
                    step: 'wifi',
                    error: { code: ev.code || '', message: errorMessage(ev.code, ev.message) },
                });

            case 'found':
                if (state.step !== 'wait') return state;
                return assign(state, {
                    step: 'done', found: { id: ev.id || state.sentId, ip: ev.ip || '', dev: ev.dev || (state.device && state.device.dev) || '' },
                    count: state.count + 1,
                });

            case 'nextDevice':
                return assign(state, {
                    step: 'kind', kind: null, device: null, rejectedId: '', idConfirmed: false, notice: null,
                    error: null, sentSsid: '', sentId: '', sentAt: null, since: null, found: null,
                    useSaved: !!state.creds, via: null, fromUsb: false, usbBands: [], usbSilent: 0, usbPort: '',
                });

            case 'restart':   // from the troubleshooting list: same device, one more go
                if (state.via === 'usb') {
                    return assign(state, {
                        step: 'usb', via: null, device: null, notice: null, error: null, usbBands: [], usbSilent: 0, usbPort: '',
                        sentId: '', sentAt: null, since: null, found: null, creds: null, useSaved: false,
                    });
                }
                return assign(state, {
                    step: 'join', device: null, rejectedId: '', idConfirmed: false, notice: null, error: null,
                    sentId: '', sentAt: null, since: null, found: null, creds: null, useSaved: false,
                });

            default:
                return state;
        }
    }

    // ---- the waiting checklist ------------------------------------------------
    // signals: {probeAnswering: bool|null, hintSsid: string|null, found: bool}
    function waitView(state, signals, nowMs) {
        signals = signals || {};
        const elapsed = state.sentAt == null ? 0 : Math.max(0, (nowMs - state.sentAt) / 1000);
        const found = !!signals.found;
        if (state.via === 'usb') {
            // Nothing to rejoin: this computer never left its Wi-Fi.
            return {
                elapsedS: Math.floor(elapsed),
                timedOut: !found && elapsed >= WAIT_TIMEOUT_S,
                items: [
                    { key: 'sent', text: TEXT.waitUsbSaved, done: true },
                    { key: 'restarted', text: TEXT.waitRestarted, done: found || elapsed >= USB_RESTART_GUESS_S },
                    { key: 'found', text: TEXT.waitFound, done: found },
                ],
            };
        }
        const restarted = found || signals.probeAnswering === false || elapsed >= RESTART_GUESS_S;
        const name = state.sentSsid;
        const hintKnown = signals.hintSsid != null;
        const back = found || (hintKnown && signals.hintSsid === name);
        return {
            elapsedS: Math.floor(elapsed),
            timedOut: !found && elapsed >= WAIT_TIMEOUT_S,
            items: [
                { key: 'restarted', text: TEXT.waitRestarted, done: restarted },
                { key: 'back', text: name ? TEXT.waitBack(name) : TEXT.waitBackUnknown, done: back },
                { key: 'found', text: TEXT.waitFound, done: found },
            ],
        };
    }

    // Is it time to ask the network to look for the device actively? Passive
    // listening can miss it when a router drops broadcasts.
    function shouldScanNow(elapsedS, lastScanS) {
        if (elapsedS < 20) return false;
        return lastScanS == null || elapsedS - lastScanS >= 25;
    }

    // ---- troubleshooting list (fixed order) -----------------------------------
    function troubleshootingSteps(os, via) {
        const T = TEXT.troubleSteps;
        os = os === 'mac' || os === 'linux' ? os : 'windows';
        const pw = via === 'usb' ? T.passwordUsb : T.password;
        return [
            { id: 'password', title: pw.title, body: pw.body, action: 'restart' },
            { id: 'band', title: T.band.title, body: T.band.body },
            { id: 'network', title: T.network.title, body: T.network.body },
            { id: 'firewall', title: T.firewall.title, body: T.firewall[os], action: 'check' },
            { id: 'isolation', title: T.isolation.title, body: T.isolation.body },
            { id: 'address', title: T.address.title, body: T.address.body, action: 'address' },
        ];
    }

    // The optional detailed check (GET /api/discovery/diagnose) may not exist in
    // this version, or may return a shape we do not know. Turn whatever came back
    // into a few plain lines. Returns {summary, lines} or null when there is
    // nothing useful to show.
    const CAUSES = {
        firewall: 'This computer\'s firewall looks like it is blocking the device. Allow Python through the firewall for private networks, then try again.',
        different_network: 'This computer and the device do not seem to be on the same network. Check the Wi-Fi icon on this computer.',
        client_isolation: 'Your router may be keeping devices apart (client isolation or a guest network). Turn that off or use a normal network.',
        device_offline: 'The device is not answering. Check that it is on and that its light shows it joined a Wi-Fi network (green, then a pulse every 3 seconds or steady orange).',
    };

    function describeDiagnose(obj) {
        if (!obj || typeof obj !== 'object') return null;
        const lines = [];
        let summary = '';
        if (typeof obj.likely_cause === 'string' && CAUSES[obj.likely_cause]) {
            summary = CAUSES[obj.likely_cause];
        } else if (typeof obj.summary === 'string') {
            summary = obj.summary;
        }
        const checks = Array.isArray(obj.checks) ? obj.checks : [];
        checks.forEach(c => {
            if (!c || typeof c !== 'object') return;
            const label = c.label || c.name || c.id;
            if (!label) return;
            const ok = c.ok === true ? 'OK' : c.ok === false ? 'Problem' : '';
            const extra = c.fix || c.message || c.detail || '';
            lines.push([ok, label, extra].filter(Boolean).join(': '));
        });
        const fixes = Array.isArray(obj.fixes) ? obj.fixes : [];
        fixes.forEach(f => { if (typeof f === 'string' && f) lines.push(f); });
        if (!summary && !lines.length) return null;
        return { summary, lines };
    }

    return {
        TEXT, WAIT_TIMEOUT_S, RESTART_GUESS_S,
        idTail, kindOf, kindName, deviceTitle, roleWord, usbBandLabel, usbBandsFrom, osFromUserAgent, suggestion,
        validateWifi, errorMessage, networkOptions, signalWord,
        initial, reduce, waitView, shouldScanNow, troubleshootingSteps, describeDiagnose,
    };
});

// OpenMuscle webcam hand labeler: decisions that do not need a camera.
//
// Three things live here so they can be unit tested under Node
// (pc/tests/js/webcam-logic.test.js) without a browser, a camera or a tracker:
//
//   1. HandSelector: which detected hand is "the" left / right hand, kept
//      stable from frame to frame (no flapping, no second hand taking over).
//   2. The tracker failure policy: GPU error -> switch to CPU once, repeated
//      CPU errors -> stop and say so.
//   3. describeError: camera / tracker errors turned into plain sentences with
//      the next step, worded for the operating system in use.
//
// No imports and no DOM, so it loads from a data: URL in the tests.

// ---------- left / right ----------

// The tracker names hands as if the picture were a mirror image. The page
// feeds it the camera's unflipped frames, so its "Left" is the person's right
// hand. Some external cameras and phone-as-webcam apps deliver an already
// mirrored picture; the Swap left/right box flips the result for those.
export function actualSide(trackerLabel, swap = false) {
    const base = String(trackerLabel).toLowerCase() === 'left' ? 'right' : 'left';
    if (!swap) return base;
    return base === 'left' ? 'right' : 'left';
}

const MIN_VOTES = 3;        // frames a new hand must be seen before it is sent
const VOTE_WINDOW = 15;     // recent frames that decide which side a hand is
const FLIP_RATIO = 1.5;     // the other side needs this much more weight to win
const MATCH_DIST = 0.25;    // max wrist movement between frames (fraction of image)
const DROP_AFTER_MS = 700;  // a hand unseen this long is forgotten

function wristDistance(a, b) {
    if (!a || !b) return 0;   // no position: let the order of detections decide
    return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

// Decides which detected hands are sent as which side.
//
// The tracker's own left/right guess is noisy for single frames (a hand edge-on
// or partly out of view can flip for one frame). Each hand is therefore
// followed as a "track" by matching wrist positions between frames, and the
// side is a weighted vote over the recent frames of that track, so one wrong
// frame never creates a second device or moves the label. Once a track is the
// chosen hand for a side it stays chosen until it is lost, so a second hand
// walking into view does not take the label over.
//
//   mode 'auto'  one hand per side, as detected
//   mode 'left' / 'right'  the operator says which hand is in view: exactly one
//                hand (the one already followed, else the clearest) is sent
//                as that side, whatever the tracker guessed
//
// update(hands, nowMs): hands = [{index, label, score, wrist: [x, y]}] with the
// tracker's own label ("Left"/"Right") and the wrist in image coordinates 0..1.
// Returns {sends: [{index, side, trackId}]}.
export class HandSelector {
    constructor(opts = {}) {
        this.swap = !!opts.swap;
        this.mode = opts.mode || 'auto';
        this.tracks = [];
        this.nextId = 1;
        this.chosen = { left: null, right: null };
        this.forced = null;
    }

    setSwap(swap) {
        if (this.swap === !!swap) return;
        this.swap = !!swap;
        this.reset();   // old votes were cast under the other meaning
    }

    setMode(mode) {
        if (this.mode === mode) return;
        this.mode = mode;
        this.chosen = { left: null, right: null };
        this.forced = null;
    }

    reset() {
        this.tracks = [];
        this.chosen = { left: null, right: null };
        this.forced = null;
    }

    update(hands, nowMs) {
        const seen = this._follow(hands || [], nowMs);
        for (const t of this.tracks) this._settleSide(t);
        const sends = this.mode === 'left' || this.mode === 'right'
            ? this._forcedPick(seen, nowMs)
            : this._autoPick(seen, nowMs);
        return { sends };
    }

    // Match this frame's detections to the hands followed so far.
    _follow(hands, nowMs) {
        const pairs = [];
        hands.forEach((h, di) => {
            this.tracks.forEach((t, ti) => {
                const d = wristDistance(h.wrist, t.wrist);
                if (d <= MATCH_DIST) pairs.push({ d, di, ti });
            });
        });
        pairs.sort((a, b) => a.d - b.d);
        const usedDet = new Set();
        const usedTrack = new Set();
        const seen = new Map();   // track -> detection
        for (const p of pairs) {
            if (usedDet.has(p.di) || usedTrack.has(p.ti)) continue;
            usedDet.add(p.di);
            usedTrack.add(p.ti);
            seen.set(this.tracks[p.ti], hands[p.di]);
        }
        hands.forEach((h, di) => {
            if (usedDet.has(di)) return;
            const t = { id: this.nextId++, wrist: h.wrist, votes: [], side: null,
                        score: h.score || 0, frames: 0, lastSeen: nowMs };
            this.tracks.push(t);
            seen.set(t, h);
        });
        for (const [t, h] of seen) {
            const w = Math.max(h.score || 0, 0.05);
            t.votes.push({ side: actualSide(h.label, this.swap), w });
            if (t.votes.length > VOTE_WINDOW) t.votes.shift();
            if (h.wrist) t.wrist = h.wrist;
            t.score = t.frames === 0 ? (h.score || 0) : 0.8 * t.score + 0.2 * (h.score || 0);
            t.frames++;
            t.lastSeen = nowMs;
        }
        this.tracks = this.tracks.filter(t => nowMs - t.lastSeen <= DROP_AFTER_MS);
        for (const side of ['left', 'right']) {
            if (this.chosen[side] && !this.tracks.some(t => t.id === this.chosen[side])) {
                this.chosen[side] = null;
            }
        }
        if (this.forced && !this.tracks.some(t => t.id === this.forced)) this.forced = null;
        return seen;
    }

    // A track's side is the weighted majority of its recent votes; it only
    // changes when the other side clearly outweighs it.
    _settleSide(t) {
        if (t.votes.length < MIN_VOTES) return;
        let left = 0;
        let right = 0;
        for (const v of t.votes) {
            if (v.side === 'left') left += v.w; else right += v.w;
        }
        if (!t.side) {
            t.side = right > left ? 'right' : 'left';
            return;
        }
        const cur = t.side === 'left' ? left : right;
        const other = t.side === 'left' ? right : left;
        if (other > cur * FLIP_RATIO) t.side = t.side === 'left' ? 'right' : 'left';
    }

    _best(tracks) {
        let best = null;
        for (const t of tracks) {
            if (!best || t.score > best.score
                || (t.score === best.score && t.frames > best.frames)) best = t;
        }
        return best;
    }

    _autoPick(seen, nowMs) {
        for (const side of ['left', 'right']) {
            const cur = this.tracks.find(t => t.id === this.chosen[side]);
            if (cur && cur.side === side) continue;   // keep the chosen hand
            this.chosen[side] = null;
            const taken = this.chosen[side === 'left' ? 'right' : 'left'];
            const pick = this._best(this.tracks.filter(t =>
                t.side === side && t.id !== taken));
            if (pick) this.chosen[side] = pick.id;
        }
        const sends = [];
        for (const side of ['left', 'right']) {
            const t = this.tracks.find(x => x.id === this.chosen[side]);
            if (!t || !seen.has(t)) continue;   // chosen hand not in this frame
            sends.push({ index: seen.get(t).index, side, trackId: t.id });
        }
        return sends;
    }

    _forcedPick(seen) {
        let t = this.tracks.find(x => x.id === this.forced);
        if (!t) {
            t = this._best(this.tracks.filter(x => x.votes.length >= MIN_VOTES));
            this.forced = t ? t.id : null;
        }
        if (!t || !seen.has(t)) return [];
        return [{ index: seen.get(t).index, side: this.mode, trackId: t.id }];
    }
}

// ---------- tracker failure policy ----------

export const MAX_CPU_ERRORS = 2;   // this many failures in a row on CPU = give up

export function newFailureState(delegate = 'GPU') {
    return { delegate, cpuErrors: 0 };
}

export function onDetectOk(state) {
    state.cpuErrors = 0;
}

// Called when detectForVideo throws. Returns what the page must do:
//   'fallback-cpu'  recreate the tracker with the CPU delegate and carry on
//   'retry'         one-off glitch on the CPU: just try the next frame
//   'stop'          it keeps failing: stop the loop and tell the operator
export function onDetectError(state) {
    if (state.delegate === 'GPU') {
        state.delegate = 'CPU';
        state.cpuErrors = 0;
        return 'fallback-cpu';
    }
    state.cpuErrors++;
    return state.cpuErrors >= MAX_CPU_ERRORS ? 'stop' : 'retry';
}

// ---------- plain error messages ----------

export function detectOS(platform = '', userAgent = '') {
    const s = `${platform} ${userAgent}`.toLowerCase();
    if (s.includes('win')) return 'windows';
    if (s.includes('mac') || s.includes('iphone') || s.includes('ipad')) return 'mac';
    if (s.includes('linux') || s.includes('x11') || s.includes('cros')) return 'linux';
    return 'other';
}

// The address to type for the camera page on this computer.
export function cameraPageUrl(loc = {}) {
    const scheme = loc.protocol === 'https:' ? 'https' : 'http';
    const port = loc.port ? `:${loc.port}` : '';
    return `${scheme}://localhost${port}/webcam`;
}

const OS_HELP = {
    permission: {
        windows: 'If it stays blocked, open Windows Settings > Privacy & security > Camera '
            + 'and turn on camera access, including "Let desktop apps access your camera".',
        mac: 'If it stays blocked, open System Settings > Privacy & Security > Camera '
            + 'and switch your browser on, then restart the browser.',
        linux: 'If it stays blocked, check the browser is allowed to use the camera '
            + '(browsers installed as a Snap or Flatpak have their own camera permission).',
        other: 'If it stays blocked, check your system settings allow this browser to use the camera.',
    },
    busy: {
        windows: 'Close the Camera app, Teams, Zoom or any other program that is using it. '
            + 'Another browser tab or window counts too.',
        mac: 'Quit FaceTime, Zoom, Photo Booth or any other program that is using it. '
            + 'Another browser tab or window counts too.',
        linux: 'Close any video-call program or other browser tab that is using it '
            + '(`fuser /dev/video0` shows what holds it).',
        other: 'Close any other program or browser tab that is using it.',
    },
    nocamera: {
        windows: 'Check it is plugged in and shows up under Settings > Bluetooth & devices > Cameras.',
        mac: 'Check it is plugged in and listed under Apple menu > About This Mac > System Report > Camera.',
        linux: 'Check it is plugged in and that `ls /dev/video*` lists it.',
        other: 'Check it is plugged in and switched on.',
    },
};

function osHelp(kind, os) {
    const t = OS_HELP[kind] || {};
    return t[os] || t.other || '';
}

const NETWORK_ERROR_RE = /dynamically imported module|importing a module script|failed to fetch|networkerror|load failed|net::|network request failed|could not (?:load|fetch)|error fetching/i;

// Turn an error into {kind, text}. text is one or more plain sentences saying
// what happened and what to do next; it never contains a stack or an error
// name on its own. env: {stage: 'camera'|'tracker'|'run', os, location,
// online, assets ('local'|'cdn'|'')}.
export function describeError(err, env = {}) {
    const name = (err && err.name) || '';
    const message = String((err && err.message) || err || '');
    const os = env.os || 'other';
    const stage = env.stage || 'camera';
    const url = cameraPageUrl(env.location || {});

    if (stage === 'camera') {
        if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
            return { kind: 'permission', text:
                'The browser was not allowed to use the camera. Click the camera icon in '
                + 'the address bar, choose Allow, then press Start camera again. '
                + osHelp('permission', os) };
        }
        if (name === 'NotReadableError' || name === 'TrackStartError' || name === 'AbortError') {
            return { kind: 'busy', text:
                'The camera is busy or could not be opened. ' + osHelp('busy', os)
                + ' Then press Start camera again.' };
        }
        if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
            return { kind: 'nocamera', text:
                'No camera was found on this computer. Plug in a webcam or switch the '
                + 'built-in one on. ' + osHelp('nocamera', os)
                + ' Then press Start camera again.' };
        }
        if (name === 'OverconstrainedError' || name === 'ConstraintNotSatisfiedError') {
            return { kind: 'constraint', text:
                'That camera cannot give the picture size this page asks for. Choose '
                + '"default camera" in the Camera list and press Start camera again.' };
        }
        if (name === 'SecurityError' || env.insecure) {
            return { kind: 'insecure', text:
                'The browser blocks the camera on this address. Open the camera page on the '
                + `computer that has the camera, at ${url} .` };
        }
        if (name === 'TypeError' && /getusermedia|undefined/i.test(message)) {
            return { kind: 'unsupported', text: unsupportedText(url) };
        }
        return { kind: 'unknown', text:
            `The camera could not be started${message ? ` (${message})` : ''}. `
            + 'Press Start camera to try again.' };
    }

    if (stage === 'tracker') {
        if (name === 'WebAssemblyUnsupported') {
            return { kind: 'unsupported', text: unsupportedText(url) };
        }
        const offline = env.online === false;
        if (offline || NETWORK_ERROR_RE.test(message)) {
            return { kind: 'offline-tracker', text:
                'Could not download the hand tracker, check internet access. The first '
                + 'run needs the internet. To work with no internet, connect once and run '
                + '`openmuscle assets fetch` in a terminal, then restart OpenMuscle.' };
        }
        return { kind: 'tracker-init', text:
            'The hand tracker could not start in this browser'
            + (message ? ` (${message})` : '')
            + '. Try the latest Chrome or Edge, then press Start camera again.' };
    }

    // stage 'run': the tracker stopped working while the camera was on.
    return { kind: 'tracker-stopped', text:
        'The hand tracker stopped working'
        + (message ? ` (${message})` : '')
        + (env.delegate === 'CPU' ? ' even after switching to the slower CPU mode' : '')
        + '. Press Start camera to try again. If it keeps happening, close other '
        + 'programs that use the camera or graphics card and reload this page.' };
}

export function unsupportedText(url) {
    return 'This browser cannot use the camera here. Use the current Chrome or Edge '
        + `(Firefox and Safari are untested) on the computer that has the camera, at ${url} .`;
}

export const GPU_FALLBACK_NOTE = 'Graphics acceleration did not work here, so the hand tracker '
    + 'switched to the CPU. It still works but may track fewer frames per second.';

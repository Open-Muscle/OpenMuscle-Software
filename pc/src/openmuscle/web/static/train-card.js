// Training result card and plain Models table: pure functions that turn the
// server's verdict / model records into the words and shapes the dashboard draws.
//
// Kept free of the DOM so the wording and the decisions (is this good enough to
// start predicting? which colour? what does a row say?) are unit tested under
// Node (pc/tests/js/train-card.test.js). Plain script (NOT an ES module)
// exposing window.OMTrain, like guide.js; under Node it is require()-able.
(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.OMTrain = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    // The server (ml/verdict.py) owns the grade; this only maps it to a colour
    // class and a one-word label.
    const TONE = { good: 'good', fair: 'fair', weak: 'weak', not_working: 'bad', untested: 'untested' };
    const WORD = { good: 'Good', fair: 'Fair', weak: 'Weak', not_working: 'Not working yet', untested: 'Not tested yet' };

    // Per-finger bar colour, same cut-offs the server uses for its wording.
    const FINGER_FOLLOWS = 0.50;
    const FINGER_LOST = 0.25;

    function toneOf(grade) { return TONE[grade] || 'untested'; }
    function wordOf(grade) { return WORD[grade] || WORD.untested; }

    function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }

    // 5-bar (or 4-bar for a finger labeler) strip: how well each finger was
    // followed on the recording the model never saw.
    function fingerBars(metrics) {
        const rows = (metrics && metrics.per_finger) || [];
        return rows.map(function (r) {
            const r2 = (r.r2 === null || r.r2 === undefined || Number.isNaN(r.r2)) ? null : Number(r.r2);
            if (r2 === null) return { name: cap(r.finger), pct: null, tone: 'flat', note: 'did not move in the test' };
            const pct = Math.max(0, Math.min(100, Math.round(r2 * 100)));
            const tone = r2 >= FINGER_FOLLOWS ? 'good' : (r2 >= FINGER_LOST ? 'fair' : 'bad');
            return { name: cap(r.finger), pct: pct, tone: tone, note: '' };
        });
    }

    function hasWarn(result) { return !!(result && result.warning); }

    // Should prediction start by itself after training? Only when the model was
    // loaded, nothing complained, and the held-out score earns Fair or Good.
    function shouldAutoStart(result) {
        if (!result || !result.active || result.activate_error || hasWarn(result)) return false;
        const v = result.verdict || {};
        return v.grade === 'fair' || v.grade === 'good';
    }

    function noteList(result) {
        return ((result && result.checks) || [])
            .filter(function (c) { return c && c.level === 'warn' && c.message; })
            .map(function (c) { return c.message; });
    }

    function technical(result) {
        const m = (result && result.metrics) || {};
        const num = function (v, d) { return (v === null || v === undefined || Number.isNaN(v)) ? 'n/a' : Number(v).toFixed(d); };
        const rows = [
            ['Held-out score (R²)', num(m.curl_r2, 3)],
            ['Held-out error (MAE)', num(m.curl_mae, 3)],
            ['How it was tested', m.eval_method || 'n/a'],
            ['Rows trained on', m.n_rows === undefined ? 'n/a' : String(m.n_rows)],
            ['Rows kept for the test', m.n_eval_rows === undefined ? 'n/a' : String(m.n_eval_rows)],
            ['Features × labels', (m.n_features === undefined ? '?' : m.n_features) + ' × ' + (m.n_labels === undefined ? '?' : m.n_labels)],
            ['Shuffled-split R² (optimistic, for old models)', num(m.r2_random_split, 3)],
        ];
        return rows;
    }

    // Everything one result card needs.
    //   tone       good | fair | weak | bad | untested  (never green for weak/bad)
    //   status     the line about whether the model is running
    function cardModel(result, title) {
        const v = (result && result.verdict) || {};
        const grade = v.grade || 'untested';
        const auto = shouldAutoStart(result);
        const loaded = !!(result && result.active && !result.activate_error);
        let status;
        if (result && result.activate_error) {
            status = 'Trained, but the model could not be loaded: ' + result.activate_error;
        } else if (result && result.warning) {
            status = 'Trained and loaded, but it cannot drive the hands: ' + result.warning;
        } else if (auto) {
            status = 'Prediction starts automatically.';
        } else if (loaded && result.paused_running) {
            status = 'Model saved but not started. Prediction was paused because this model is not good enough yet.';
        } else if (loaded) {
            status = 'Model saved but not started.';
        } else {
            status = 'Model saved but not loaded.';
        }
        return {
            title: title || '',
            tone: toneOf(grade),
            grade: grade,
            headline: v.headline || 'Trained',
            detail: v.detail || '',
            nextStep: v.next_step || '',
            bars: fingerBars((result && result.metrics) || {}),
            autoStart: auto,
            canStartAnyway: loaded && !auto && !hasWarn(result),
            status: status,
            notes: noteList(result),
            badTakes: ((result && result.bad_captures) || []).map(function (b) {
                return b.name + ((b.reasons && b.reasons.length) ? ' (' + b.reasons[0] + ')' : '');
            }),
            savedTo: (result && (result.model_dir || result.model_path)) || '',
            technical: technical(result),
        };
    }

    // One card per hand for a two-hand result, else a single card. `payload` is
    // either the /api/train response (possibly per_hand) or {left, right} built
    // by the client from two role-specific calls.
    function cardsFor(payload) {
        if (!payload) return [];
        const sides = payload.results || (payload.left && payload.right ? payload : null);
        if (sides) {
            return ['left', 'right'].filter(function (s) { return sides[s]; })
                .map(function (s) { return cardModel(sides[s], cap(s) + ' hand'); });
        }
        return [cardModel(payload, '')];
    }

    function allAutoStart(cards) {
        return cards.length > 0 && cards.every(function (c) { return c.autoStart; });
    }

    // ---- Models table ----------------------------------------------------

    function handLabel(m) {
        const r = m && m.metrics && m.metrics.role;
        return r === 'left' ? 'Left band' : (r === 'right' ? 'Right band' : 'One band');
    }

    // The code as typed (participant_label), else the stored key.
    function participantCode(m) {
        return String((m && (m.participant_label || m.participant)) || '').trim();
    }

    function participantLabel(m) {
        const p = participantCode(m);
        return p ? p : 'unknown participant';
    }

    // "2026-10-09 14:03" from the registry's created stamp (YYYYmmdd_HHMMSS).
    function whenText(created) {
        const s = String(created || '');
        const x = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/.exec(s);
        return x ? x[1] + '-' + x[2] + '-' + x[3] + ' ' + x[4] + ':' + x[5] : s;
    }

    function bandUnits(m) {
        const ids = (m && m.band_device_ids) || {};
        const out = [];
        Object.keys(ids).forEach(function (k) {
            (ids[k] || []).forEach(function (d) { if (out.indexOf(d) < 0) out.push(d); });
        });
        return out;
    }

    function scoreText(m) {
        const v = (m && m.verdict) || {};
        const word = wordOf(v.grade);
        if (v.score_pct === null || v.score_pct === undefined || v.grade === 'untested') return word;
        if (v.grade === 'not_working') return word;
        return word + ' (' + v.score_pct + '%)';
    }

    // "5.3 MB", "820 KB", "1.2 GB"; '?' when the size is not known.
    function sizeText(bytes) {
        if (typeof bytes !== 'number' || !isFinite(bytes) || bytes < 0) return '?';
        if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + ' GB';
        if (bytes >= 1e6) return (bytes / 1e6).toFixed(bytes >= 1e8 ? 0 : 1) + ' MB';
        if (bytes >= 1e3) return Math.round(bytes / 1e3) + ' KB';
        return bytes + ' B';
    }

    // A model trained on the five finger curls (the default) predicts five numbers
    // in 0..1 instead of a 3D hand; this is the one-line live readout for them,
    // "Thumb 12%  Index 80% ...". null when the values are not five curls.
    const CURL_NAMES = ['Thumb', 'Index', 'Middle', 'Ring', 'Pinky'];
    function curlSummary(values) {
        if (!Array.isArray(values) || values.length !== CURL_NAMES.length) return null;
        if (!values.every(function (v) { return typeof v === 'number' && isFinite(v); })) return null;
        return CURL_NAMES.map(function (n, i) {
            return n + ' ' + Math.round(Math.max(0, Math.min(1, values[i])) * 100) + '%';
        }).join('  ');
    }

    // Models trained before the size cap can be hundreds of megabytes; say so,
    // so the Delete button has an obvious reason to exist.
    const BIG_MODEL_BYTES = 100e6;

    function sizeTitle(bytes) {
        if (typeof bytes !== 'number' || !isFinite(bytes)) return 'size on disk not known';
        return bytes >= BIG_MODEL_BYTES
            ? 'Large: this model was trained with every hand joint (or before models were kept small). Training again makes a much smaller one; Delete frees this space.'
            : 'Disk space this model takes';
    }

    function modelRow(m) {
        const v = (m && m.verdict) || {};
        const metrics = (m && m.metrics) || {};
        const num = function (x, d) { return (x === null || x === undefined || Number.isNaN(x)) ? 'n/a' : Number(x).toFixed(d); };
        return {
            name: (m && (m.dir || m.name)) || '',
            when: whenText(m && m.created),
            hand: handLabel(m),
            participant: participantLabel(m),
            unknownParticipant: !participantCode(m),
            units: bandUnits(m),
            session: (m && m.session_name) || '',
            tone: toneOf(v.grade),
            score: scoreText(m),
            scoreTitle: v.headline || '',
            size: sizeText(m && m.size_bytes),
            sizeTitle: sizeTitle(m && m.size_bytes),
            featuresLabels: (metrics.n_features === undefined ? '?' : metrics.n_features) + ' × ' + (metrics.n_labels === undefined ? '?' : metrics.n_labels),
            mse: num(metrics.mse, 4),
            r2: num(metrics.curl_r2 !== undefined && metrics.curl_r2 !== null ? metrics.curl_r2 : metrics.r2, 3),
        };
    }

    // ---- participant / band guard wording --------------------------------

    function normCode(s) { return String(s || '').trim().toLowerCase(); }
    function sameParticipant(a, b) { return normCode(a) === normCode(b); }

    // Text for the blocking confirm shown before prediction starts on a mismatch.
    function guardPrompt(issues) {
        const lines = (issues || []).map(function (i) { return i.message; });
        return lines.join('\n\n') + '\n\nPress OK to run it anyway, or Cancel to keep prediction off '
            + '(then train a new model for this participant and these bands).';
    }

    return {
        toneOf: toneOf, wordOf: wordOf, fingerBars: fingerBars, shouldAutoStart: shouldAutoStart,
        cardModel: cardModel, cardsFor: cardsFor, allAutoStart: allAutoStart,
        handLabel: handLabel, participantLabel: participantLabel, participantCode: participantCode, whenText: whenText,
        scoreText: scoreText, modelRow: modelRow, sizeText: sizeText, sizeTitle: sizeTitle, curlSummary: curlSummary, bandUnits: bandUnits,
        normCode: normCode, sameParticipant: sameParticipant, guardPrompt: guardPrompt,
        FINGER_FOLLOWS: FINGER_FOLLOWS, FINGER_LOST: FINGER_LOST,
    };
});

// Recording status wording: the big timer, one plain remedy sentence per
// problem, and the technical counters that live under "Details".
//
// The verdict itself (good / check / bad and its flags) is computed on the
// server (quality.py) and arrives in the live recording payload and in the
// stop result; this module only turns it into words. Kept free of the DOM so
// the wording is unit tested under Node (pc/tests/js/rec-status.test.js).
// Plain script exposing window.OMRecStatus, like guide.js; under Node the same
// file is require()-able.
(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.OMRecStatus = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const WORD = { good: 'Good', check: 'Check', bad: 'Bad' };

    // 83.4 -> "1:23", 7 -> "0:07", 3725 -> "1:02:05".
    function formatTimer(seconds) {
        const total = Math.max(0, Math.floor(Number(seconds) || 0));
        const h = Math.floor(total / 3600);
        const m = Math.floor((total % 3600) / 60);
        const s = total % 60;
        const ss = String(s).padStart(2, '0');
        return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
    }

    function lengthAdvice(limits) {
        if (!limits) return '';
        return `Record at least ${limits.min_seconds} s; ${limits.recommended_seconds} s or more works best.`;
    }

    // One plain sentence for a quality flag ({code, severity, reason}). The
    // server's own reason is the fallback for a flag this build does not know.
    function remedy(flag, limits) {
        const bad = flag.severity === 'bad';
        switch (flag.code) {
            case 'empty':
                return 'Nothing was recorded. Check that the band and the finger-data source are on and sending, then record again.';
            case 'short':
                return limits ? `Too short to train on. ${lengthAdvice(limits)}` : (flag.reason || '');
            case 'weak_signal':
                return 'The band is not picking up pressure. Make sure it is snug on your arm, then record again.';
            case 'no_labels':
                return bad
                    ? 'No finger data arrived. Check that the finger-data source is on and in view, then record again.'
                    : 'This take has band data only, with no finger data, so it cannot be used to train a model.';
            case 'low_match':
                return bad
                    ? 'Almost no finger data came through. Check that the finger-data source is on and in view, then record again.'
                    : 'Some finger data went missing during the take. It can still be used, but a cleaner take is better.';
            case 'idle_labeler':
                return 'Your hand barely moved during the take, so there is little to learn from. Open and close your hand through the whole take.';
            case 'hand_lost':
                return bad
                    ? 'Hand tracking lost your hand for most of the take. Keep your hand in view and record again.'
                    : 'Hand tracking dropped out a few times during the take. Keep your hand in view.';
            default:
                return flag.reason || '';
        }
    }

    function sentencesFor(quality, limits, skipCodes) {
        const skip = skipCodes || [];
        const flags = (quality && quality.flags) || [];
        if (flags.length) {
            return flags.filter(f => skip.indexOf(f.code) === -1)
                .map(f => remedy(f, limits)).filter(Boolean);
        }
        // Verdict from an older server: no flags, only the reasons text.
        return ((quality && quality.reasons) || []).slice();
    }

    // While recording, how the take is going for length.
    function lengthHint(durationS, limits) {
        if (!limits) return '';
        const d = Number(durationS) || 0;
        if (d < limits.min_seconds) {
            return `Keep recording: at least ${limits.min_seconds} s, and ${limits.recommended_seconds} s or more works best.`;
        }
        if (d < limits.recommended_seconds) {
            return `Long enough to train on. ${limits.recommended_seconds} s or more works best.`;
        }
        return 'Plenty of data. Stop whenever you are ready.';
    }

    function pct(x) { return `${(Number(x || 0) * 100).toFixed(1)}%`; }

    // Status for a take in progress. `rec` is the live recording payload.
    function live(rec) {
        const r = rec || {};
        const q = r.quality || { verdict: 'good', flags: [], reasons: [] };
        const sensors = r.sensors || {};
        const bandIds = Object.keys(sensors);
        const bands = bandIds.length > 1
            ? 'Bands: ' + bandIds.map(id => `${sensors[id]} ${id}`).join(', ')
            : `Band: ${r.sensor_device_id || '?'}`;
        const seen = r.sensor_frames_seen || 0;
        return {
            timer: formatTimer(r.duration_s),
            tone: q.verdict || 'good',
            headline: q.verdict === 'bad' ? 'Recording, but there is a problem'
                : (q.verdict === 'check' ? 'Recording, but check this' : 'Recording'),
            sentences: sentencesFor(q, r.limits, ['short']),
            hint: lengthHint(r.duration_s, r.limits),
            details: [
                `File: ${r.filename || '?'}`,
                `Rows saved so far: ${r.rows || 0}`,
                bands,
                `Finger data from: ${r.label_device_id || 'none (band data only)'}`,
                `Frames matched with finger data: ${r.matched || 0} of ${seen} (${pct(r.match_rate)})`,
                `Frames with no finger data: ${r.unpaired_sensor || 0}`,
                `Finger-data packets received: ${r.label_packets_seen || 0}`,
                `Time window: ${r.window_ms || 100} ms`,
            ],
        };
    }

    // Status for a take that was just saved. `result` is the stop payload.
    function saved(result) {
        const r = result || {};
        const q = r.quality || { verdict: 'good', flags: [], reasons: [] };
        const tooShort = !!r.too_short;
        const sentences = [];
        if (tooShort && r.short_note) {
            // "Too short to train on (6 s). Record at least 10 s; ..." after "Saved, but".
            sentences.push('Saved, but ' + r.short_note.charAt(0).toLowerCase() + r.short_note.slice(1));
        }
        sentences.push(...sentencesFor(q, r.limits, tooShort ? ['short'] : []));
        if (!sentences.length) sentences.push('This take looks good.');
        return {
            timer: formatTimer(r.duration_s),
            tone: q.verdict || 'good',
            headline: `Saved: ${WORD[q.verdict] || 'Good'}`,
            tooShort,
            sentences,
            details: [
                `File: ${r.filename || '?'}`,
                `Rows saved: ${r.rows || 0}`,
                `Length: ${r.duration_s || 0} s`,
                `Frames matched with finger data: ${r.matched || 0} of ${r.sensor_frames_seen || 0} (${pct(r.match_rate)})`,
                `Finger data from: ${r.label_device_id || 'none (band data only)'}`,
            ],
        };
    }

    return { WORD, formatTimer, lengthAdvice, remedy, lengthHint, live, saved };
});

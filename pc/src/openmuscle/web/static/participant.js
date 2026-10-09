// Participant code helpers for the session and capture forms.
//
// Recordings carry a code (P01), never a person's name. These pure functions
// decide when a typed value looks like a name (so the form can warn, softly:
// the value still saves) and how a session's code and arm are spelled in the
// UI. Kept free of the DOM so they are unit tested under Node
// (pc/tests/js/participant.test.js). Plain script exposing window.OMParticipant,
// like guide.js; under Node the same file is require()-able.
(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.OMParticipant = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const PLACEHOLDER = 'P01 (a code, not a name)';
    const HELPER = 'Please do not type names. Keep the key that links codes to people somewhere else.';
    const WARNING = 'That looks like a name. Please use a code such as P01 instead, and keep the '
        + 'key that links codes to people somewhere else. You can still save it as typed.';

    // Two capitalised words run together or joined by - _ . (JaneSmith, Jane_Smith).
    const TWO_WORDS = /^[A-Z][a-z]+[-_.]?[A-Z][a-z]+$/;

    // True when the value has a space (two words) or reads like a first and last
    // name. An empty value is fine: the code is optional.
    function looksLikeName(value) {
        const v = String(value == null ? '' : value).trim();
        if (!v) return false;
        if (/\s/.test(v)) return true;
        if (v.indexOf('@') !== -1) return true;      // an e-mail address
        return TWO_WORDS.test(v);
    }

    // The soft warning text for a typed value, or '' when there is nothing to say.
    function nameWarning(value) {
        return looksLikeName(value) ? WARNING : '';
    }

    // A session's participant code. Older sessions stored it as wearer or subject.
    function codeOf(session) {
        const s = session || {};
        return String(s.wearer || s.subject || '').trim();
    }

    // "left arm" | "right arm" | "both arms" | '' for an unset arm.
    function armText(arm) {
        if (arm === 'left') return 'left arm';
        if (arm === 'right') return 'right arm';
        if (arm === 'both') return 'both arms';
        return '';
    }

    return { PLACEHOLDER, HELPER, WARNING, looksLikeName, nameWarning, codeOf, armText };
});

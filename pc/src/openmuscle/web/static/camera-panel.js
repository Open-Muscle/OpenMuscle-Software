// Dashboard Camera panel: shows the webcam hand labeler (/webcam?embed=1) in an
// iframe so tracking runs in the same visible tab as the Record button. A tab in
// the background stops drawing frames, which pauses hand tracking; this removes
// the need for a second window.
//
// The iframe is only created the first time the panel is opened (no camera
// prompt before the operator asks for it) and is never torn down when the panel
// is collapsed, so a running camera keeps running. The page inside reports its
// state with postMessage; that is shown in the panel header.
(function () {
    'use strict';
    const panel = document.getElementById('camera-panel');
    if (!panel) return;
    const toggle = document.getElementById('camera-panel-toggle');
    const body = document.getElementById('camera-panel-body');
    const stateEl = document.getElementById('camera-panel-state');
    const STORE_OPEN = 'om.camera_panel_open';
    let frame = null;

    function remembered() {
        try { return localStorage.getItem(STORE_OPEN) === '1'; } catch (e) { return false; }
    }
    function remember(open) {
        try { localStorage.setItem(STORE_OPEN, open ? '1' : '0'); } catch (e) { /* lasts this load */ }
    }

    function ensureFrame() {
        if (frame) return;
        if (!window.isSecureContext) {
            // Browsers only allow the camera on http://localhost (or https).
            body.innerHTML = '<p class="camera-panel-hint">The camera only works when this page is '
                + 'opened on the computer that has the camera, at <b>http://localhost:'
                + (location.port || '8000') + '</b>.</p>';
            frame = true;
            return;
        }
        frame = document.createElement('iframe');
        frame.id = 'camera-frame';
        frame.title = 'Webcam hand labeler';
        frame.src = '/webcam?embed=1';
        frame.setAttribute('allow', 'camera');
        body.appendChild(frame);
    }

    function setOpen(open) {
        panel.classList.toggle('collapsed', !open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        toggle.textContent = (open ? '\u25BE' : '\u25B8') + ' Camera';
        if (open) ensureFrame();
        remember(open);
    }

    toggle.addEventListener('click', () => setOpen(panel.classList.contains('collapsed')));

    // Opening it from elsewhere (for example a link in a hint).
    window.OMCameraPanel = { open: () => setOpen(true) };

    window.addEventListener('message', ev => {
        if (ev.origin !== location.origin) return;
        const m = ev.data;
        if (!m || m.type !== 'om-webcam-state') return;
        let text = 'camera off';
        let cls = '';
        if (m.running) {
            text = 'camera on, ' + String(m.hand || 'no hand').replace(/^tracking: /, '');
            cls = /^tracking/.test(m.hand || '') ? 'on' : 'warn';
            if (/^paused/.test(m.hand || '')) cls = 'warn';
        } else if (/failed|stopped/.test(m.tracker || '')) {
            text = 'camera problem';
            cls = 'bad';
        } else if (/^camera:/.test(m.hand || '')) {
            text = m.hand;
            cls = 'bad';
        }
        stateEl.textContent = text;
        stateEl.className = 'camera-panel-state' + (cls ? ' ' + cls : '');
    });

    setOpen(remembered());
})();

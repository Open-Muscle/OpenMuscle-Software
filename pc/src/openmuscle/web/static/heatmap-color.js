// Shared FlexGrid heatmap color ramp.
//
// SINGLE SOURCE OF TRUTH for the band-heatmap colors, so the main dashboard
// (app.js) and the OBS /stream view (stream.js) can never drift apart. Tory
// glances between the live dashboard and the streamed window; matching colors
// are the whole point of extracting this.
//
// Plain script (NOT an ES module) exposing window.OMHeatColor, because app.js
// and gamepad.js both load as plain <script> tags without the importmap.
// Turning app.js into a module to `import` this would risk the working
// dashboard, so we share via a global instead. app.js keeps a byte-identical
// fallback so a missing/blocked load never breaks the live heatmap.
//
// The ramp + noise gate here are copied verbatim from app.js's pressureColor
// (see static/app.js). If you change one, change the other; the values are
// duplicated on purpose so each file renders correctly even in isolation.
(function () {
    'use strict';

    // Below this ADC value a cell is treated as untouched (stays near the bg).
    const NOISE_GATE = 8;

    // "Inferno"-style ramp with a visible low end. Anything above the noise
    // gate gets a perceptible color; only truly idle cells stay near the bg.
    const STOPS = [
        [40,  45,  90 ],   // soft blue (just-above-noise)
        [85,  40,  140],   // purple
        [165, 45,  140],   // magenta
        [225, 90,  90 ],   // pink/red
        [255, 165, 60 ],   // orange
        [255, 230, 90 ],   // yellow
    ];

    // Background for idle/untouched cells (matches app.js drawHeatmapInto bg).
    const IDLE = '#1a1f2b';

    // v = raw ADC value, vmax = value that maps to the top of the ramp.
    function pressureColor(v, vmax) {
        if (v < NOISE_GATE) return IDLE;
        const t = Math.max(0, Math.min(1, v / vmax));
        const seg = Math.min(STOPS.length - 2, Math.floor(t * (STOPS.length - 1)));
        const localT = (t * (STOPS.length - 1)) - seg;
        const a = STOPS[seg], b = STOPS[seg + 1];
        const lerp = (x, y) => Math.round(x + (y - x) * localT);
        return `rgb(${lerp(a[0], b[0])},${lerp(a[1], b[1])},${lerp(a[2], b[2])})`;
    }

    window.OMHeatColor = {
        NOISE_GATE,
        IDLE,
        pressureColor,
    };
})();

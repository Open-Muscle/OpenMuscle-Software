"""Capture-quality primitives: cell-engagement tracking.

The dud-take trap (board #0311.4): capture_1779731309 looks like a normal file
(plausible per-cell baselines, paired labels) but 50/60 cells have a value RANGE
under 5 counts across the whole take -- the band was effectively not being worn /
not engaged, so the file trains nothing. Match-rate can't catch this (labels
matched fine); only per-cell activity can.

CellActivityTracker keeps per-cell min/max over a recording (cheap: one compare
pair per cell per frame) so the live verdict can flag a flat take IN PROGRESS,
and the same logic doubles as the offline regression check.
"""

# A cell whose (max - min) stays under this many ADC counts across the take is
# "flat" (baseline noise only; the dud's 50 flat cells all sat under 5).
FLAT_RANGE_COUNTS = 5

# Verdict rule: after enough frames to be meaningful, a take with at least this
# fraction of its cells flat means the band is not engaged.
FLAT_FRACTION_BAD = 0.8
MIN_FRAMES_FOR_VERDICT = 20


class CellActivityTracker:
    """Per-cell min/max over the flattened sensor rows of one recording."""

    def __init__(self):
        self._min = None
        self._max = None
        self.frames = 0

    def update(self, flat_row) -> None:
        """Feed one flattened sensor row (row-major R{r}C{c} values)."""
        if not flat_row:
            return
        if self._min is None:
            self._min = list(flat_row)
            self._max = list(flat_row)
        else:
            n = min(len(flat_row), len(self._min))
            for i in range(n):
                v = flat_row[i]
                if v < self._min[i]:
                    self._min[i] = v
                elif v > self._max[i]:
                    self._max[i] = v
        self.frames += 1

    @property
    def n_cells(self) -> int:
        return len(self._min) if self._min else 0

    def flat_cells(self, threshold: float = FLAT_RANGE_COUNTS) -> int:
        """How many cells never moved more than `threshold` counts."""
        if not self._min:
            return 0
        return sum(1 for lo, hi in zip(self._min, self._max)
                   if (hi - lo) < threshold)

    def is_flat_take(self) -> bool:
        """True when this take is a capture_1779731309-style dud: enough frames
        seen and >= FLAT_FRACTION_BAD of the cells never engaged."""
        n = self.n_cells
        if self.frames < MIN_FRAMES_FOR_VERDICT or n == 0:
            return False
        return self.flat_cells() >= FLAT_FRACTION_BAD * n

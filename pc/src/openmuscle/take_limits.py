"""How long a take has to be: one set of numbers for the whole program.

The recorder, the captures table, the post-stop card and the trainer all ask
this module, so the wording and the thresholds cannot drift apart. A take
shorter than the minimum is still saved and listed (it is never hidden or
deleted); it is just tagged "too short" and left out of the one-click Train
selection.
"""

from typing import Optional

# Shorter than this is probably an accidental click, or too little to learn from.
MIN_TAKE_SECONDS = 10.0
MIN_TAKE_ROWS = 50
# What to aim for when recording a take.
RECOMMENDED_TAKE_SECONDS = 30.0
# A saved take that has no stored row count (recorded by an older version) is
# judged by file size instead: under 50 KB is roughly a few seconds.
MIN_FALLBACK_BYTES = 51200


def as_dict() -> dict:
    """The limits in a JSON-friendly shape, for the live recording payload."""
    return {
        "min_seconds": MIN_TAKE_SECONDS,
        "min_rows": MIN_TAKE_ROWS,
        "recommended_seconds": RECOMMENDED_TAKE_SECONDS,
    }


def is_too_short(rows: Optional[int] = None, duration_s: Optional[float] = None,
                 size_bytes: Optional[int] = None) -> bool:
    """True when a take is below the minimum length or row count.

    Uses duration and rows when known; with neither, falls back to file size.
    """
    if duration_s is not None and duration_s < MIN_TAKE_SECONDS:
        return True
    if rows is not None and rows < MIN_TAKE_ROWS:
        return True
    if rows is None and duration_s is None and size_bytes is not None:
        return size_bytes < MIN_FALLBACK_BYTES
    return False


def advice() -> str:
    """The recording advice, as one sentence fragment."""
    return "Record at least {} s; {} s or more works best.".format(
        int(MIN_TAKE_SECONDS), int(RECOMMENDED_TAKE_SECONDS))


def short_note(rows: Optional[int] = None,
               duration_s: Optional[float] = None) -> str:
    """Plain-language note for a take that is too short, with the advice."""
    if duration_s is not None and duration_s < MIN_TAKE_SECONDS:
        what = "{} s".format(int(round(duration_s)))
    elif rows is not None and rows < MIN_TAKE_ROWS:
        what = "{} rows".format(rows)
    else:
        return "Too short to train on. " + advice()
    return "Too short to train on ({}). {}".format(what, advice())

"""Plain-language verdict for a trained model: the one place that turns the
trainer's metrics into words a non-engineer can act on.

Everything the UI and CLI say about "is this model any good" comes from
describe_result(); nothing else should re-derive a grade. The cut-offs live in
THRESHOLDS so they can be tuned in one spot without touching logic or tests of
the wording.

The grade is based on the honest held-out score (metrics["curl_r2"], measured on
a recording or time span the model never trained on), never on the optimistic
shuffled-split number.
"""

# Held-out curl R2 cut-offs. A score at or above a line earns that grade.
THRESHOLDS = {
    "good": 0.75,
    "fair": 0.50,
    "weak": 0.25,
}

# Per-finger wording cut-offs (same scale, applied to each finger's own R2).
FINGER_THRESHOLDS = {
    "follows": 0.50,        # at or above: "follows well"
    "not_learned": 0.25,    # below: "is not learned yet"
}

# A tail of the same take can only show the model repeats a movement it has
# already seen, so it can never earn better than this.
TAIL_GRADE_CAP = "fair"

GRADE_ORDER = ("not_working", "weak", "fair", "good")

# Grades that justify starting live prediction without a second thought.
SHOW_LIVE_OK = ("fair", "good")

_HEADLINES = {
    "good": "Good",
    "fair": "Fair",
    "weak": "Weak",
    "not_working": "Not working yet",
}


def _grade_for(score, thresholds):
    if score >= thresholds["good"]:
        return "good"
    if score >= thresholds["fair"]:
        return "fair"
    if score >= thresholds["weak"]:
        return "weak"
    return "not_working"


def _join(names):
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _cap(s):
    return s[:1].upper() + s[1:]


def _finger_sentence(per_finger, thresholds, space):
    """'Index and middle follow well; ring is not learned yet.' (or '')."""
    if space not in ("curl", "piston") or not per_finger:
        return ""
    follows, partial, lost, flat = [], [], [], []
    for row in per_finger:
        name = row.get("finger")
        r2 = row.get("r2")
        if r2 is None:
            flat.append(name)
        elif r2 >= thresholds["follows"]:
            follows.append(name)
        elif r2 < thresholds["not_learned"]:
            lost.append(name)
        else:
            partial.append(name)
    scored = len(follows) + len(partial) + len(lost)
    parts = []
    if scored and len(follows) == scored and not flat:
        parts.append("Every finger follows well")
    else:
        if follows:
            parts.append("{} {} well".format(
                _cap(_join(follows)), "follows" if len(follows) == 1 else "follow"))
        if partial:
            parts.append("{} {} partly tracked".format(
                _cap(_join(partial)), "is" if len(partial) == 1 else "are"))
        if lost:
            parts.append("{} {} not learned yet".format(
                _cap(_join(lost)), "is" if len(lost) == 1 else "are"))
    text = "; ".join(parts)
    if flat:
        note = "{} did not move in the test recording, so {} could not be checked".format(
            _cap(_join(flat)), "it" if len(flat) == 1 else "they")
        text = (text + ". " + note) if text else note
    return text + "." if text else ""


def _percent(score):
    return max(0, int(round(score * 100)))


def describe_result(metrics, checks=None, n_captures=None, hand_side=None,
                    thresholds=None, finger_thresholds=None):
    """Turn trainer metrics into plain words.

    Args:
        metrics: the dict train_model() returns / the registry stores.
        checks: optional list of {level, message, ...} data checks; their
            warnings are appended to `detail`.
        n_captures: how many captures went into the model (None = unknown).
        hand_side: 'left' | 'right' | None for a one-hand / unspecified model.
        thresholds, finger_thresholds: override the module dicts (tests).

    Returns dict:
        grade        'good' | 'fair' | 'weak' | 'not_working' | 'untested'
        headline     one short line for the card title
        detail       one or two sentences on what the score means
        next_step    the one thing to do next
        show_live_ok True when it is reasonable to start live prediction
        score_pct    int 0..100 (never negative) or None
        capped       True when the grade was held at the tail cap
    """
    th = dict(THRESHOLDS if thresholds is None else thresholds)
    fth = dict(FINGER_THRESHOLDS if finger_thresholds is None else finger_thresholds)
    metrics = metrics or {}
    method = metrics.get("eval_method")
    score = metrics.get("curl_r2")
    space = metrics.get("score_space")
    side = {"left": "left-hand ", "right": "right-hand "}.get(hand_side or "", "")

    warn_text = ""
    if checks:
        msgs = [str(c.get("message")) for c in checks
                if isinstance(c, dict) and c.get("level") == "warn" and c.get("message")]
        if msgs:
            warn_text = " Heads up: " + " ".join(m if m.endswith(".") else m + "." for m in msgs)

    # No honest number: a legacy model (trained before held-out scoring existed)
    # or a recording too short to hold anything out.
    if score is None or method in (None, "none", "random_split"):
        if method == "none":
            detail = ("There were too few rows to keep any aside for a fair test, so "
                      "this model has not been tested on anything it did not train on.")
            nxt = "Record a longer take (a minute or more of moving the fingers) and train again."
        else:
            detail = ("This model was trained before held-out testing existed, so its "
                      "saved score came from rows it had already seen and is too "
                      "optimistic to trust.")
            nxt = "Train again on your recordings to get an honest score."
        return {"grade": "untested", "headline": "Not tested yet",
                "detail": detail + warn_text, "next_step": nxt,
                "show_live_ok": False, "score_pct": None, "capped": False}

    grade = _grade_for(float(score), th)
    capped = False
    if method == "held_out_tail" and (GRADE_ORDER.index(grade)
                                      > GRADE_ORDER.index(TAIL_GRADE_CAP)):
        grade, capped = TAIL_GRADE_CAP, True
    pct = _percent(float(score))

    if method == "held_out_capture":
        n_eval = len(metrics.get("eval_captures") or []) or 1
        how = "on {} recording{} it never saw".format(
            n_eval, "" if n_eval == 1 else "s")
    else:
        how = "on the last part of the same recording it trained on"

    what = "finger movement" if space in ("curl", "piston") else "the recorded signal"
    if grade == "not_working":
        headline = _HEADLINES[grade]
        pieces = ["Tested {}, the {}model does not predict {} yet.".format(how, side, what)]
    else:
        headline = "{}: explains about {}% of {}".format(_HEADLINES[grade], pct, what)
        pieces = ["Tested {}{}.".format(
            how, " ({}model)".format(side) if side else "")]
    fingers = _finger_sentence(metrics.get("per_finger"), fth, space)
    if fingers:
        pieces.append(fingers)
    if method == "held_out_tail":
        pieces.append(
            "Held at Fair because this test used the end of the same take; "
            "test on a separate recording to confirm." if capped else
            "This test used the end of the same take; a separate recording "
            "is a stronger test.")
    detail = " ".join(pieces) + warn_text

    if grade == "good":
        nxt = ("Go try live prediction. To be sure it holds up tomorrow, "
               "record one more take and test on it.")
    elif grade == "fair":
        nxt = ("It follows the big movements. Record more takes (including "
               "each finger on its own) and train again.")
    elif grade == "weak":
        nxt = ("Check the band sits snug on the same spot of the arm, record "
               "a rest take plus slow single-finger takes, then train again.")
    else:
        nxt = ("Check the band is snug on the arm and the labeler is moving, "
               "then record new takes and train again.")
    if n_captures is not None and n_captures < 2 and grade != "good":
        nxt += " Using two or more separate recordings also gives a fairer test."

    return {"grade": grade, "headline": headline,
            "detail": detail, "next_step": nxt,
            "show_live_ok": grade in SHOW_LIVE_OK,
            "score_pct": pct, "capped": capped}

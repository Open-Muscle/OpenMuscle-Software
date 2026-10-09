"""Export bundle: one zip with a session's takes and a plain-language README.

The zip is what a person emails or uploads to hand their recordings to someone
else. It holds, for every take, the CSV (the recording itself) and its small
helper files, plus three files generated here:

    README.txt    every file in the zip explained in plain English, how to open
                  the CSV, the units, and how the quality verdict was reached
    session.json the session (devices, settings, participant code); synthesized
                  for loose takes that were never part of a session
    summary.csv   one row per take: when, how long, rows, bands, labeler,
                  quality verdict and reasons, participant code

Compact by default: the raw .sensor.jsonl / .label.jsonl streams are left out
(they are large and only needed to re-process a take), notes are left out
unless asked for, and trained models are never included (a model file is a
pickle, which is not safe to open from someone else).

Nothing is read into memory whole: files are copied into the zip in chunks, and
the CSVs are scanned row by row when a quality verdict has to be worked out.

Library use:

    report = build_session_bundle(captures_dir, sessions_dir, "out.zip",
                                  session_id="2026-05-16T08-42-13")
    report = build_session_bundle(captures_dir, sessions_dir, "out.zip",
                                  capture_names=["a.csv", "b.csv"])
    info = estimate_bundle(captures_dir, sessions_dir, session_id=...)
"""

import csv
import io
import json
import os
import textwrap
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from openmuscle import quality as _q

# A bundle bigger than this is awkward to email; callers show a warning.
WARN_BYTES = 20 * 1024 * 1024

_COPY_CHUNK = 1 << 20

_SIDECAR_SUFFIXES = (".meta.json", ".labels.schema.json")
_RAW_SUFFIXES = (".sensor.jsonl", ".label.jsonl")

# Plain names for the label_source values written into a take's meta.
_LABELER_NAMES = {
    "lask5": "LASK5 finger labeler",
    "quest": "VR headset hand tracking",
    "quest_hand": "VR headset hand tracking",
    "webcam": "webcam hand tracking",
    "webcam_hand": "webcam hand tracking",
    "gamepad": "gamepad",
    "manual": "typed in by hand",
}


class BundleError(ValueError):
    """The bundle cannot be built. `kind` is "not_found" or "invalid"."""

    kind = "invalid"


class BundleNotFound(BundleError):
    kind = "not_found"


@dataclass
class BundleReport:
    """What was written (or, for an estimate, what would be)."""
    out_file: Optional[Path]
    session_id: Optional[str]
    takes: List[str]                      # capture file names, in bundle order
    files: List[str]                      # every path inside the zip
    uncompressed_bytes: int
    zip_bytes: int
    include_raw: bool
    include_notes: bool
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "out_file": str(self.out_file) if self.out_file else None,
            "filename": self.out_file.name if self.out_file else None,
            "session_id": self.session_id,
            "takes": list(self.takes),
            "files": list(self.files),
            "uncompressed_bytes": self.uncompressed_bytes,
            "zip_bytes": self.zip_bytes,
            "include_raw": self.include_raw,
            "include_notes": self.include_notes,
            "warnings": list(self.warnings),
            "warn_large": self.zip_bytes > WARN_BYTES,
        }


# ---------------------------------------------------------------------------
# Names and lookup
# ---------------------------------------------------------------------------

def clean_capture_name(name) -> str:
    """A capture file name safe to join onto the captures folder, always ending
    in .csv. Anything that could point outside the folder is rejected."""
    raw = str(name or "").strip()
    if not raw:
        raise BundleError("A capture name is empty.")
    if (any(c in raw for c in ("/", "\\", ":", "\x00")) or ".." in raw
            or raw.startswith(".")):
        raise BundleError("Not a valid capture name: {!r}".format(raw))
    if not raw.lower().endswith(".csv"):
        raw += ".csv"
    return raw


def _clean_session_id(session_id) -> str:
    raw = str(session_id or "").strip()
    if (not raw or any(c in raw for c in ("/", "\\", ":", "\x00"))
            or ".." in raw or raw.startswith(".")):
        raise BundleError("Not a valid session id: {!r}".format(raw))
    return raw


def load_session(sessions_dir, session_id) -> dict:
    """The session JSON for `session_id`; BundleNotFound when there is none."""
    sid = _clean_session_id(session_id)
    path = Path(sessions_dir) / (sid + ".json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise BundleNotFound("No session named {!r}.".format(sid))
    except (OSError, ValueError) as e:
        raise BundleError("Session {!r} could not be read: {}".format(sid, e))
    if not isinstance(data, dict):
        raise BundleError("Session {!r} is not a valid session file.".format(sid))
    data.setdefault("id", sid)
    return data


def find_session(sessions_dir, ref) -> dict:
    """A session by id, or by name when the name is unambiguous."""
    ref = str(ref or "").strip()
    if not ref:
        raise BundleError("Say which session to export.")
    try:
        return load_session(sessions_dir, ref)
    except BundleNotFound:
        pass
    hits = []
    folder = Path(sessions_dir)
    for p in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                s = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(s, dict) and str(s.get("name", "")).strip().lower() == ref.lower():
            s.setdefault("id", p.stem)
            hits.append(s)
    if not hits:
        raise BundleNotFound("No session with the id or name {!r}.".format(ref))
    if len(hits) > 1:
        raise BundleError("More than one session is named {!r}; use its id: {}".format(
            ref, ", ".join(h["id"] for h in hits)))
    return hits[0]


def bundle_filename(session_id: Optional[str] = None, now: Optional[float] = None) -> str:
    """File name for a bundle. Built from the session id (a timestamp) or the
    export time, never from a session name, which might hold a person's name."""
    if session_id:
        keep = "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(session_id))
        return "openmuscle-session-{}.zip".format(keep.strip("-") or "export")
    return "openmuscle-takes-{}.zip".format(
        time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time())))


def unique_path(folder, filename) -> Path:
    """folder/filename, or folder/name-2.zip ... when that is taken. Never
    overwrites an earlier export."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / filename
    stem, suffix = p.stem, p.suffix
    n = 2
    while p.exists():
        p = folder / "{}-{}{}".format(stem, n, suffix)
        n += 1
    return p


# ---------------------------------------------------------------------------
# Gathering the takes
# ---------------------------------------------------------------------------

@dataclass
class _Take:
    name: str                 # "x.csv"
    stem: str                 # "x"
    csv: Path
    meta: dict
    meta_path: Optional[Path]
    schema_path: Optional[Path]
    raw_paths: List[Path]
    created_s: float = 0.0


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _gather(captures_dir, sessions_dir, session_id, capture_names):
    """-> (session dict or None, [_Take], [warning])"""
    captures_dir = Path(captures_dir)
    warnings: List[str] = []
    session = None
    if session_id and capture_names:
        raise BundleError("Give either a session or a list of takes, not both.")
    if session_id:
        session = load_session(sessions_dir, session_id)
        names = [clean_capture_name(n) for n in (session.get("captures") or [])]
        strict = False
    elif capture_names:
        names = [clean_capture_name(n) for n in capture_names]
        strict = True
    else:
        raise BundleError("Say which session or which takes to export.")

    root = captures_dir.resolve()
    takes: List[_Take] = []
    seen = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        path = captures_dir / name
        try:
            inside = path.resolve().parent == root
        except OSError:
            inside = False
        if not inside:
            raise BundleError("Not a valid capture name: {!r}".format(name))
        if not path.is_file():
            if strict:
                raise BundleNotFound("Capture not found: {}".format(name))
            warnings.append("{} is listed in the session but its file is missing, so "
                            "it is not in this bundle.".format(name))
            continue
        stem = name[:-4]
        meta_path = captures_dir / (stem + ".meta.json")
        schema_path = captures_dir / (stem + ".labels.schema.json")
        meta = _read_json(meta_path) if meta_path.is_file() else {}
        t = _Take(
            name=name, stem=stem, csv=path, meta=meta,
            meta_path=meta_path if meta_path.is_file() else None,
            schema_path=schema_path if schema_path.is_file() else None,
            raw_paths=[captures_dir / (stem + s) for s in _RAW_SUFFIXES
                       if (captures_dir / (stem + s)).is_file()])
        created_ms = meta.get("created_ms")
        if isinstance(created_ms, (int, float)) and created_ms > 0:
            t.created_s = created_ms / 1000.0
        else:
            started = (meta.get("auto") or {}).get("started_at")
            t.created_s = float(started) if isinstance(started, (int, float)) else path.stat().st_mtime
        takes.append(t)
    if not takes:
        raise BundleError("There are no recordings to export"
                          + (" in this session." if session_id else "."))
    takes.sort(key=lambda t: (t.created_s, t.name))
    return session, takes, warnings


# ---------------------------------------------------------------------------
# Per-take facts (summary.csv)
# ---------------------------------------------------------------------------

def _take_facts(take: _Take, session: Optional[dict], include_notes: bool,
                scan: bool = True) -> dict:
    meta = take.meta
    auto = meta.get("auto") if isinstance(meta.get("auto"), dict) else {}
    final = auto.get("final") if isinstance(auto.get("final"), dict) else {}
    qual = _q.quality_of(final)
    rows = final.get("rows")
    secs = final.get("duration_s")
    if scan and (qual is None or rows is None or secs is None):
        # Older or unfinished take: read the CSV once to work the numbers out.
        try:
            stats = _q.scan_capture_csv(take.csv, meta.get("label_source"))
        except (OSError, ValueError, csv.Error):
            stats = None
        if stats is not None:
            if rows is None:
                rows = stats["rows"]
            if secs is None:
                secs = stats["duration_s"]
            if qual is None:
                merged = dict(final)
                merged.update(stats)
                s = _q.summarize_take(merged)
                qual = {"verdict": s["verdict"], "reasons": s["reasons"]}

    roles = meta.get("roles")
    band_roles = sorted({str(r) for r in roles.values()}) if isinstance(roles, dict) else []
    label_src = str(meta.get("label_source") or auto.get("label_source") or "").strip().lower()
    if label_src in _LABELER_NAMES:
        labeler = _LABELER_NAMES[label_src]
    elif label_src:
        labeler = label_src
    elif final.get("has_labeler") is False:
        labeler = "none (sensor only)"
    else:
        labeler = "unknown"

    session = session or {}
    code = ""
    for cand in (auto.get("session_wearer"), meta.get("subject"),
                 session.get("wearer"), session.get("subject")):
        if cand and str(cand).strip():
            code = str(cand).strip()
            break
    return {
        "file": "takes/" + take.name,
        "start_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(take.created_s)),
        "seconds": secs if secs is not None else "",
        "rows": rows if rows is not None else "",
        "bands": " + ".join(band_roles) if band_roles else "",
        "labeler": labeler,
        "quality": qual["verdict"] if qual else "not checked",
        "quality_reasons": " | ".join(qual["reasons"]) if qual else "",
        "participant_code": code,
        "notes": (str(meta.get("notes") or "").replace("\r", " ").replace("\n", " ")
                  if include_notes else ""),
    }


_SUMMARY_COLUMNS = ["file", "start_time", "seconds", "rows", "bands", "labeler",
                    "quality", "quality_reasons", "participant_code", "notes"]


def _summary_csv(facts: Sequence[dict], include_notes: bool) -> str:
    cols = [c for c in _SUMMARY_COLUMNS if include_notes or c != "notes"]
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(cols)
    for f in facts:
        w.writerow([f[c] for c in cols])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Generated files
# ---------------------------------------------------------------------------

def _session_json(session: Optional[dict], takes: Sequence[_Take],
                  facts: Sequence[dict], include_notes: bool) -> str:
    if session is not None:
        out = {k: v for k, v in session.items() if not k.startswith("_")}
        out["synthesized"] = False
    else:
        out = {
            "id": None,
            "name": "",
            "synthesized": True,
            "note": "These takes were not part of a session; this file was "
                    "generated when they were exported.",
            "exported_at": time.time(),
        }
        codes = sorted({f["participant_code"] for f in facts if f["participant_code"]})
        out["wearer"] = codes[0] if len(codes) == 1 else ""
    out["captures"] = [t.name for t in takes]
    out["capture_count"] = len(takes)
    if not include_notes:
        out["notes"] = ""
    return json.dumps(out, indent=2, default=str) + "\n"


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("openmuscle")
    except Exception:
        from openmuscle import __version__
        return __version__


def _readme(session: Optional[dict], takes: Sequence[_Take], facts: Sequence[dict],
            include_raw: bool, include_notes: bool, warnings: Sequence[str],
            take_files: dict, generated_at: float) -> str:
    L: List[str] = []
    add = L.append

    add("OpenMuscle export")
    add("=================")
    add("")
    add("This zip file holds recordings made with OpenMuscle: a forearm pressure-sensor")
    add("band (and, usually, something that measures finger movement at the same time).")
    add("It was packed on {} by OpenMuscle {}.".format(
        time.strftime("%Y-%m-%d %H:%M", time.localtime(generated_at)), _version()))
    add("You do not need OpenMuscle to read it: every file is plain text (CSV or JSON).")
    add("")

    codes = sorted({f["participant_code"] for f in facts if f["participant_code"]})
    total_s = sum(f["seconds"] for f in facts if isinstance(f["seconds"], (int, float)))
    add("What is in it")
    add("-------------")
    if session is not None:
        add("Session:          {}".format(session.get("name") or session.get("id")))
        add("Session id:       {}".format(session.get("id")))
    else:
        add("Session:          none (these takes were exported on their own)")
    add("Takes:            {}".format(len(takes)))
    if total_s:
        add("Total recorded:   {:.0f} seconds".format(total_s))
    add("Participant code: {}".format(", ".join(codes) if codes else "(none entered)"))
    add("")
    add("A take is one recording, from pressing Record to pressing Stop. Takes started")
    add("inside a session are grouped under it in session.json.")
    add("")

    add("The files")
    add("---------")
    add("README.txt")
    add("    This file.")
    add("session.json")
    if session is not None:
        add("    The session: which devices were used, their settings, the participant")
        add("    code, and the list of takes.")
    else:
        add("    The list of takes and the devices used. These takes were not part of a")
        add("    session, so this file was generated for the export.")
    add("summary.csv")
    add("    One row per take: when it was recorded, how long, how many rows, which")
    add("    band(s), what measured the fingers, the quality verdict and why, and the")
    add("    participant code" + (", plus your notes." if include_notes else "."))
    add("    Opens in Excel, Numbers or any spreadsheet.")
    add("")
    add("For each take, in the takes/ folder:")
    for t in takes:
        add("")
        for arc, what in take_files[t.name]:
            add(arc)
            add(textwrap.fill(what, 78, initial_indent="    ", subsequent_indent="    "))
    add("")

    add("How to open a recording (the .csv files)")
    add("-----------------------------------------")
    add("Double-click it to open it in a spreadsheet, or read it with any data tool")
    add("(for example Python: pandas.read_csv). The first row holds the column names.")
    add("Each following row is one moment in time.")
    add("")
    add("Columns, left to right:")
    add("  ts_hub_ms      time in milliseconds on the recording computer's clock. Only")
    add("                 differences between rows mean anything. (Older files have a")
    add("                 'timestamp' column in seconds instead.)")
    add("  role           which band made the row: left or right (where it was worn).")
    add("  device_id      the band's own identifier.")
    add("  R0C0 ... R3C14 the pressure readings, one column per sensor cell: 4 rows by")
    add("                 15 columns, 60 cells per band. Raw numbers from the band's")
    add("                 sensor chip (about 0 to 4095) that change as the arm presses on")
    add("                 the cell. They have no unit such as newtons; compare them to")
    add("                 each other, not to a scale.")
    add("  label_0 ...    the finger measurements recorded at the same moment: the")
    add("                 answer a model learns to predict. For the LASK5 these are the")
    add("                 readings of its finger sensors; for a camera or headset they are hand")
    add("                 joint positions (see the .labels.schema.json file when there")
    add("                 is one). A take recorded without a labeler has none of these.")
    add("  imu_*, lbl_imu_*, forearm_roll_deg, palm_up, lbl_flex_*, lbl_ang_*")
    add("                 extra columns that appear only when the recording used them:")
    add("                 motion-sensor readings, forearm rotation, and finger bend")
    add("                 normalised to 0 to 1 (flex) or in degrees (ang).")
    add("")

    add("About the participant code")
    add("-------------------------")
    add("Takes are labelled with a short code chosen by whoever ran the recording")
    add("(for example P01), not a name. The list that links codes to people is kept")
    add("separately and is not part of this file.")
    add("")

    add("Band roles and the labeler")
    add("--------------------------")
    add("'left' and 'right' say which forearm a band was on. The 'labeler' column in")
    add("summary.csv says what measured the fingers: a LASK5 finger labeler, a webcam,")
    add("a VR headset, or nothing (a sensor-only take, which cannot train a model).")
    add("")

    add("How the quality verdict was worked out")
    add("--------------------------------------")
    add("Every take gets one word, good, check or bad, decided by OpenMuscle when the")
    add("recording stopped (or, for older files, by reading the CSV). It looks at:")
    add("  - Size: a take with no rows is bad; under {:.0f} seconds or {} rows is check.".format(
        _q.MIN_SECONDS, _q.MIN_ROWS))
    add("  - Band engagement: a sensor cell is 'flat' if it never moved by {} or more".format(
        _q.FLAT_RANGE_COUNTS))
    add("    counts. If {:.0f}% or more of the cells are flat the band was not worn or".format(
        _q.FLAT_FRACTION_BAD * 100))
    add("    not touching the arm, and the take is bad.")
    add("  - Labels: if labels matched fewer than {:.0f}% of sensor frames the take is".format(
        _q.MATCH_RATE_BAD * 100))
    add("    bad, under {:.0f}% is check. No labels at all is bad (or check for a".format(
        _q.MATCH_RATE_CHECK * 100))
    add("    sensor-only take).")
    add("  - A labeler that barely moved, or hand tracking that kept dropping out, is")
    add("    check or bad depending on how much.")
    add("The reasons are written out in words in summary.csv. A verdict is a guide: it")
    add("says whether a take looks usable, not whether the movements were the right ones.")
    add("")

    add("What was left out")
    add("-----------------")
    if include_raw:
        add("Raw streams are included (the .sensor.jsonl and .label.jsonl files): the")
        add("untouched packets from the devices, so a take can be re-processed. They make")
        add("the zip much larger.")
    else:
        add("Raw streams (.sensor.jsonl, .label.jsonl) were not included, to keep the zip")
        add("small. The CSVs hold everything needed to look at or train on the data.")
    if include_notes:
        add("Notes you typed (on the session and on each take) are included. They are")
        add("free text, so check they hold no names.")
    else:
        add("Notes you typed were not included.")
    add("Trained models are never included.")
    for w in warnings:
        add("Note: " + w)
    add("")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Plan: which files go in, under which names
# ---------------------------------------------------------------------------

_TAKE_DESCRIPTIONS = {
    ".csv": "The recording: one row per moment, pressure readings plus finger labels.",
    ".meta.json": "Settings for this take (bands, labeler, timing) and its quality result.",
    ".labels.schema.json": "Names for the label columns of this take (which joint or finger "
                           "each one is).",
    ".sensor.jsonl": "Raw band packets exactly as received, one per line (large; for "
                     "re-processing).",
    ".label.jsonl": "Raw labeler packets exactly as received, one per line (large; for "
                    "re-processing).",
}


def _plan_take_files(takes: Sequence[_Take], include_raw: bool, include_notes: bool):
    """Per take: [(arcname, source Path or None for generated, description)].
    Returns (plan dict by name, descriptions dict by name)."""
    plan = {}
    desc = {}
    for t in takes:
        items = [("takes/" + t.name, t.csv, _TAKE_DESCRIPTIONS[".csv"])]
        if t.meta_path is not None:
            d = _TAKE_DESCRIPTIONS[".meta.json"]
            if not include_notes:
                d += " Your notes were removed."
            items.append(("takes/" + t.stem + ".meta.json", t.meta_path, d))
        if t.schema_path is not None:
            items.append(("takes/" + t.stem + ".labels.schema.json", t.schema_path,
                          _TAKE_DESCRIPTIONS[".labels.schema.json"]))
        if include_raw:
            for rp in t.raw_paths:
                suffix = ".sensor.jsonl" if rp.name.endswith(".sensor.jsonl") else ".label.jsonl"
                items.append(("takes/" + rp.name, rp, _TAKE_DESCRIPTIONS[suffix]))
        plan[t.name] = items
        desc[t.name] = [(a, d) for a, _p, d in items]
    return plan, desc


def _meta_text(meta_path: Path, include_notes: bool) -> Optional[bytes]:
    """Bytes of a meta.json for the zip; free-text fields blanked unless notes
    are included. None when it cannot be made safe (unreadable and notes off)."""
    if include_notes:
        try:
            return meta_path.read_bytes()
        except OSError:
            return None
    data = _read_json(meta_path)
    if not data:
        return None
    if "notes" in data:
        data["notes"] = ""
    data.pop("extras", None)
    return (json.dumps(data, indent=2, default=str) + "\n").encode("utf-8")


def _build_generated(session, takes, include_raw, include_notes, warnings, scan):
    plan, desc = _plan_take_files(takes, include_raw, include_notes)
    facts = [_take_facts(t, session, include_notes, scan=scan) for t in takes]
    generated = {
        "session.json": _session_json(session, takes, facts, include_notes),
        "summary.csv": _summary_csv(facts, include_notes),
    }
    generated["README.txt"] = _readme(session, takes, facts, include_raw, include_notes,
                                      warnings, desc, time.time())
    return plan, generated


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _zip_date(mtime: float):
    t = time.localtime(mtime)[:6]
    return t if t[0] >= 1980 else (1980, 1, 1, 0, 0, 0)


def build_session_bundle(captures_dir, sessions_dir, out_file,
                         session_id: Optional[str] = None,
                         capture_names: Optional[Sequence[str]] = None,
                         include_raw: bool = False,
                         include_notes: bool = False) -> BundleReport:
    """Write the export zip to `out_file` and return a BundleReport.

    Give a session_id (every take in the session) or capture_names (loose
    takes). The zip is written to a temporary name and moved into place when
    complete, so a failed export never leaves a half-written file behind.
    """
    session, takes, warnings = _gather(captures_dir, sessions_dir, session_id, capture_names)
    plan, generated = _build_generated(session, takes, include_raw, include_notes,
                                       warnings, scan=True)

    out = Path(out_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    arcnames: List[str] = []
    uncompressed = 0
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for arc in ("README.txt", "session.json", "summary.csv"):
                data = generated[arc].encode("utf-8")
                zi = zipfile.ZipInfo(arc, date_time=_zip_date(time.time()))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o644 << 16
                zf.writestr(zi, data)
                arcnames.append(arc)
                uncompressed += len(data)
            for t in takes:
                for arc, src, _d in plan[t.name]:
                    if arc.endswith(".meta.json"):
                        data = _meta_text(src, include_notes)
                        if data is None:
                            warnings.append("{} could not be read and was left out."
                                            .format(src.name))
                            continue
                        zi = zipfile.ZipInfo(arc, date_time=_zip_date(src.stat().st_mtime))
                        zi.compress_type = zipfile.ZIP_DEFLATED
                        zi.external_attr = 0o644 << 16
                        zf.writestr(zi, data)
                        arcnames.append(arc)
                        uncompressed += len(data)
                        continue
                    st = src.stat()
                    zi = zipfile.ZipInfo(arc, date_time=_zip_date(st.st_mtime))
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zi.external_attr = 0o644 << 16
                    written = 0
                    with open(src, "rb") as fh, zf.open(
                            zi, "w", force_zip64=st.st_size >= (1 << 31)) as dest:
                        while True:
                            chunk = fh.read(_COPY_CHUNK)
                            if not chunk:
                                break
                            dest.write(chunk)
                            written += len(chunk)
                    arcnames.append(arc)
                    uncompressed += written
        os.replace(tmp, out)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return BundleReport(
        out_file=out, session_id=(session or {}).get("id") if session else None,
        takes=[t.name for t in takes], files=arcnames,
        uncompressed_bytes=uncompressed, zip_bytes=out.stat().st_size,
        include_raw=include_raw, include_notes=include_notes, warnings=warnings)


# ---------------------------------------------------------------------------
# Estimate
# ---------------------------------------------------------------------------

_SAMPLE_BYTES = 192 * 1024


def _deflated_size(data: bytes) -> int:
    c = zlib.compressobj(6, zlib.DEFLATED, -15)
    return len(c.compress(data)) + len(c.flush())


def _estimate_compressed(path: Path) -> int:
    """About how many bytes `path` will take in the zip. Small files are
    compressed for real; big ones are sampled (start, middle, end) and the
    ratio applied to the whole, which tracks the true size closely for CSV."""
    size = path.stat().st_size
    if size == 0:
        return 0
    with open(path, "rb") as fh:
        if size <= 3 * _SAMPLE_BYTES:
            return _deflated_size(fh.read())
        raw = comp = 0
        for off in (0, (size - _SAMPLE_BYTES) // 2, size - _SAMPLE_BYTES):
            fh.seek(off)
            chunk = fh.read(_SAMPLE_BYTES)
            raw += len(chunk)
            comp += _deflated_size(chunk)
    return int(size * (comp / float(raw)))


def estimate_bundle(captures_dir, sessions_dir,
                    session_id: Optional[str] = None,
                    capture_names: Optional[Sequence[str]] = None,
                    include_raw: bool = False,
                    include_notes: bool = False) -> dict:
    """How big the zip will be, without building it. Also reports the other
    option (with / without raw streams) so a dialog can offer the choice."""
    session, takes, warnings = _gather(captures_dir, sessions_dir, session_id, capture_names)

    def total(raw: bool):
        plan, generated = _build_generated(session, takes, raw, include_notes,
                                           warnings, scan=False)
        unc = comp = n_files = 0
        for text in generated.values():
            b = text.encode("utf-8")
            unc += len(b)
            comp += _deflated_size(b)
            n_files += 1
        for t in takes:
            for arc, src, _d in plan[t.name]:
                n_files += 1
                if arc.endswith(".meta.json"):
                    data = _meta_text(src, include_notes) or b""
                    unc += len(data)
                    comp += _deflated_size(data)
                else:
                    unc += src.stat().st_size
                    comp += _estimate_compressed(src)
                comp += 2 * len(arc) + 110       # local + central directory entries
        return unc, comp + 22, n_files

    unc, comp, n_files = total(include_raw)
    if any(t.raw_paths for t in takes):
        c_unc, c_comp, _n = total(False)
        r_unc, r_comp, _n = total(True)
    else:
        c_unc, c_comp = r_unc, r_comp = unc, comp
    return {
        "session_id": (session or {}).get("id") if session else None,
        "takes": len(takes),
        "files": n_files,
        "include_raw": include_raw,
        "include_notes": include_notes,
        "uncompressed_bytes": unc,
        "estimated_zip_bytes": comp,
        "compact_zip_bytes": c_comp,
        "with_raw_zip_bytes": r_comp,
        "warn_over_bytes": WARN_BYTES,
        "warn_large": comp > WARN_BYTES,
        "warnings": warnings,
    }

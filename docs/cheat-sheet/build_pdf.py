#!/usr/bin/env python3
"""Print the cheat sheet HTML to a PDF with a headless Chromium browser.

Usage:
    python build_pdf.py                 # paper size comes from the browser default
    python build_pdf.py --paper a4      # force A4
    python build_pdf.py --paper letter  # force US Letter

Reads  openmuscle-kit-cheat-sheet.html  and writes  openmuscle-kit-cheat-sheet.pdf
next to this script. Needs Microsoft Edge or Google Chrome (or Chromium) installed;
nothing else. Set OPENMUSCLE_BROWSER to the browser's full path to override the search.
Exits 0 on success and 1 on any failure (no browser, no PDF, or a page count outside 2 to 4).
"""
import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = HERE / "openmuscle-kit-cheat-sheet.html"
TARGET = HERE / "openmuscle-kit-cheat-sheet.pdf"
MIN_PAGES, MAX_PAGES = 2, 4


def find_browser():
    override = os.environ.get("OPENMUSCLE_BROWSER")
    if override:
        return override if pathlib.Path(override).is_file() else None
    candidates = []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates += [
                pathlib.Path(base) / "Microsoft/Edge/Application/msedge.exe",
                pathlib.Path(base) / "Google/Chrome/Application/chrome.exe",
            ]
    candidates += [
        pathlib.Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        pathlib.Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        pathlib.Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    for name in ("microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable",
                 "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def count_pages(pdf_bytes):
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", pdf_bytes))


def wait_for_pdf(path, timeout_s):
    """The browser can return before it has finished writing, so wait for a stable file."""
    deadline = time.time() + timeout_s
    last_size = -1
    while time.time() < deadline:
        if path.is_file():
            size = path.stat().st_size
            if size > 1000 and size == last_size:
                return True
            last_size = size
        time.sleep(0.7)
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paper", choices=("auto", "a4", "letter"), default="auto")
    args = ap.parse_args()

    if not SOURCE.is_file():
        print("error: missing " + str(SOURCE), file=sys.stderr)
        return 1
    browser = find_browser()
    if not browser:
        print("error: no Edge, Chrome or Chromium found. Install one, or set OPENMUSCLE_BROWSER to its full path.",
              file=sys.stderr)
        return 1

    html_for_print = SOURCE
    temp_html = None
    if args.paper != "auto":
        size = {"a4": "A4", "letter": "letter"}[args.paper]
        text = SOURCE.read_text(encoding="utf-8")
        text = text.replace("@page{ size:auto;", "@page{ size:" + size + ";", 1)
        temp_html = HERE / (".print-%d.html" % os.getpid())
        temp_html.write_text(text, encoding="utf-8")
        html_for_print = temp_html

    profile = tempfile.mkdtemp(prefix="om-cheat-sheet-")
    if TARGET.exists():
        TARGET.unlink()
    cmd = [
        browser,
        "--user-data-dir=" + profile,  # a fresh profile, so a browser you already have open is left alone
        "--no-first-run", "--no-default-browser-check",
        "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
        "--print-to-pdf=" + str(TARGET),
        html_for_print.as_uri(),
    ]
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ok = wait_for_pdf(TARGET, 90)
        if not ok:
            print("error: the browser did not produce a PDF within 90 seconds.", file=sys.stderr)
            return 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()  # only the process this script started
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if temp_html is not None and temp_html.exists():
            temp_html.unlink()
        shutil.rmtree(profile, ignore_errors=True)

    data = TARGET.read_bytes()
    if not data.startswith(b"%PDF"):
        print("error: output is not a PDF.", file=sys.stderr)
        return 1
    pages = count_pages(data)
    print("wrote %s (%d pages, %d KB)" % (TARGET, pages, len(data) // 1024))
    if not (MIN_PAGES <= pages <= MAX_PAGES):
        print("error: expected %d to %d pages, got %d. Check the layout." % (MIN_PAGES, MAX_PAGES, pages), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

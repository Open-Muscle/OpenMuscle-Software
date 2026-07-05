"""Repair a gamepad capture whose header undercounts the label columns.

Captures recorded before the gamepad label-width fix wrote a header with only
label_0..label_3 (the 4 axes) while every DATA row carried the full axes+buttons
vector (Xbox = 21 values). pandas then read more fields than header names,
shifted the columns, and training failed with "no rows with role=left".

The data itself is fine and rectangular; only the header is short. This inserts
the missing label_N names in the correct position (right after the last existing
label_ column, before the imu block) so header width == row width. Writes
<name>_fixed.csv next to the input; the original is never touched.

Usage: python fix_gamepad_capture_header.py path/to/capture.csv
"""

import csv
import sys
from pathlib import Path


def repair(path: str) -> str:
    src = Path(path)
    with open(src, newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        first_row = next(reader)

    delta = len(first_row) - len(header)
    if delta == 0:
        print(f"{src.name}: header already matches rows ({len(header)} cols), nothing to do")
        return str(src)
    if delta < 0:
        raise SystemExit(f"{src.name}: rows are NARROWER than the header ({len(first_row)} < {len(header)}); not a gamepad-header case")

    label_idx = [i for i, c in enumerate(header) if c.startswith("label_")]
    if not label_idx:
        raise SystemExit(f"{src.name}: no label_ columns in header; can't place the missing ones")
    last_i = label_idx[-1]
    last_n = max(int(header[i].split("_")[1]) for i in label_idx)
    new_names = [f"label_{last_n + 1 + k}" for k in range(delta)]
    new_header = header[: last_i + 1] + new_names + header[last_i + 1:]
    assert len(new_header) == len(first_row), (len(new_header), len(first_row))

    dst = src.with_name(src.stem + "_fixed.csv")
    with open(src, newline="") as fin, open(dst, "w", newline="") as fout:
        r = csv.reader(fin)
        w = csv.writer(fout)
        next(r)                    # drop the short header
        w.writerow(new_header)     # write the repaired header
        rows = 0
        for row in r:
            w.writerow(row)
            rows += 1
    print(f"{src.name}: header {len(header)} -> {len(new_header)} cols "
          f"(added {new_names[0]}..{new_names[-1]}); wrote {dst.name} ({rows} rows)")
    return str(dst)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python fix_gamepad_capture_header.py <capture.csv>")
    repair(sys.argv[1])

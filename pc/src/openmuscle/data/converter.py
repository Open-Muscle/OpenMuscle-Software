"""Convert legacy capture formats to standard CSV."""

import ast
import csv
import os
from pathlib import Path


def convert_legacy_capture(input_path: str, output_path: str) -> int:
    """Convert a legacy capture_*.txt file to standard CSV format.

    Legacy format: one Python dict repr per line, e.g.:
        {'id': 'OM-LASK5', 'ticks': 164587, 'time': (2000, 1, 1, ...), 'data': [-30, -35, -30, -37]}

    Args:
        input_path: path to legacy .txt capture file
        output_path: path for output .csv file

    Returns:
        Number of rows written
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    rows_written = 0

    with open(input_path, "r") as f_in, open(output_path, "w", newline="") as f_out:
        writer = csv.writer(f_out)
        header_written = False

        for line in f_in:
            line = line.strip()
            if not line:
                continue
            try:
                pkt = ast.literal_eval(line)
            except Exception:
                continue

            if not isinstance(pkt, dict) or "data" not in pkt:
                continue

            device_id = pkt.get("id", "unknown")
            ticks = pkt.get("ticks", 0)
            data = pkt["data"]

            if not header_written:
                n = len(data)
                header = ["device_id", "ticks"] + [f"value_{i}" for i in range(n)]
                writer.writerow(header)
                header_written = True

            writer.writerow([device_id, ticks] + data)
            rows_written += 1

    return rows_written

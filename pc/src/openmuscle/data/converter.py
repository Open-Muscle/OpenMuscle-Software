"""Convert legacy capture formats to standard CSV with packet matching."""

import ast
import csv
import os
from collections import deque
from pathlib import Path


# Legacy device IDs for SensorBand (3 banks × 4 sensors = 12 total)
SENSOR_IDS = ['OM-SB-V1-C.0', 'OM-SB-V1-C.1', 'OM-SB-V1-C.2']
LABEL_ID = 'OM-LASK5'


def convert_legacy_capture(input_path: str, output_path: str) -> int:
    """Convert a legacy capture_*.txt file to matched CSV format.

    Matches sensor packets from 3 SensorBand banks with LASK5 label packets
    by temporal proximity, producing rows with 12 sensor values + 4 labels.

    Legacy format: one Python dict repr per line, e.g.:
        {'id': 'OM-SB-V1-C.0', 'ticks': 164587, 'data': [2686.1, 2926.1, 2519.7, 2653.1], ...}

    Output CSV columns:
        Sensor_0..Sensor_11, Sensor_Timestamp, Label_0..Label_3, Label_Timestamp

    Args:
        input_path: path to legacy .txt capture file
        output_path: path for output .csv file

    Returns:
        Number of rows written
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    buffers = {sid: deque(maxlen=5) for sid in SENSOR_IDS}
    buffers[LABEL_ID] = deque(maxlen=5)

    header = ([f"Sensor_{i}" for i in range(12)] + ['Sensor_Timestamp'] +
              [f"Label_{i}" for i in range(4)] + ['Label_Timestamp'])

    rows_written = 0
    last_record = None

    with open(input_path, "r") as f_in, open(output_path, "w", newline="") as f_out:
        writer = csv.writer(f_out)
        writer.writerow(header)

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

            pkt_id = pkt.get("id")
            if pkt_id not in buffers:
                continue

            buffers[pkt_id].append(pkt)

            # Try to match: need at least one packet from each sensor bank + labels
            if not all(buffers[sid] for sid in SENSOR_IDS):
                continue
            if not buffers[LABEL_ID]:
                continue

            # Combine latest from each sensor bank
            sensor_values = []
            sensor_times = []
            for sid in SENSOR_IDS:
                latest = buffers[sid][-1]
                sensor_values.extend(latest.get('data', []))
                sensor_times.append(latest.get('rec_time', 0))

            label_pkt = buffers[LABEL_ID][-1]
            labels = label_pkt.get('data', [])
            label_time = label_pkt.get('rec_time', 0)
            sensor_time = min(sensor_times)

            record = sensor_values + [sensor_time] + labels + [label_time]

            # Skip duplicate records (same data as last write)
            if record == last_record:
                continue

            if len(record) == 18:
                writer.writerow(record)
                rows_written += 1
                last_record = record

    return rows_written


def _summarize_header(header: list) -> dict:
    """Compact column-shape summary for a mismatch error message."""
    import re
    return {
        "cols": len(header),
        "sensor": sum(1 for c in header if re.match(r"^R\d+C\d+$", c)),
        "label": sum(1 for c in header if c.startswith("label_")),
        "imu": sum(1 for c in header if c.startswith("imu_") or c.startswith("lbl_imu_")),
        "forearm": sum(1 for c in header if c in ("forearm_roll_deg", "palm_up")),
    }


def combine_csvs(csv_paths: list[str], output_path: str) -> int:
    """Concatenate multiple SCHEMA-COMPATIBLE CSVs into one file.

    Every input MUST share the exact same header (same sensor R{r}C{c}, label_*,
    and optional imu_/forearm_ columns, in the same order). This is the
    anti-corruption guard from docs/data-schema.md: before, this helper wrote the
    first file's header and blind-appended the other bodies, so mixing captures of
    different width (a 4-label LASK5 capture with a 175-label Quest capture, or a
    12-cell V1 band with a 60-cell V4 band) SILENTLY produced a ragged, corrupt
    CSV. It now validates the headers up front and refuses instead.

    role/device_id are ROW values in schema-v2, so left+right (or multi-band)
    captures of the same schema concat cleanly -- that is the separable-per-hand
    path. Pooling DIFFERENT label sources into one model is the canonical-label
    merge (a separate, schema-aware path, docs/data-schema.md), not this helper.

    Args:
        csv_paths: CSV file paths to combine (all the same schema).
        output_path: path for the combined output CSV.

    Returns:
        Total number of data rows written.

    Raises:
        ValueError: if csv_paths is empty, any file is empty/headerless, or any
            file's header differs from the first (the message names the file and
            shows each header's column-shape so the mismatch is obvious).
    """
    if not csv_paths:
        raise ValueError("combine_csvs: no input files given")

    # Validate schema compatibility BEFORE writing anything, so a mismatch never
    # leaves a half-written (corrupt) output file behind.
    ref_header = None
    ref_path = None
    for csv_path in csv_paths:
        with open(csv_path, "r") as f_in:
            reader = csv.reader(f_in)
            try:
                header = next(reader)
            except StopIteration:
                raise ValueError(
                    f"combine_csvs: {Path(csv_path).name} is empty (no header row)")
        if ref_header is None:
            ref_header, ref_path = header, csv_path
        elif header != ref_header:
            raise ValueError(
                "combine_csvs: refusing to merge captures with different schemas "
                "(this would corrupt the CSV).\n"
                f"  {Path(ref_path).name}: {_summarize_header(ref_header)}\n"
                f"  {Path(csv_path).name}: {_summarize_header(header)}\n"
                "All captures must share the same columns to concatenate. To pool "
                "different label sources (e.g. a VR session + a LASK5 session) into "
                "one model, use the canonical-label merge (docs/data-schema.md), "
                "not combine_csvs.")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(output_path, "w", newline="") as f_out:
        writer = csv.writer(f_out)
        writer.writerow(ref_header)
        for csv_path in csv_paths:
            with open(csv_path, "r") as f_in:
                reader = csv.reader(f_in)
                next(reader)   # skip the (already-validated) header
                for row in reader:
                    writer.writerow(row)
                    total += 1

    return total

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


def combine_csvs(csv_paths: list[str], output_path: str) -> int:
    """Concatenate multiple CSVs (same schema) into one file.

    Args:
        csv_paths: list of CSV file paths to combine
        output_path: path for the combined output CSV

    Returns:
        Total number of data rows written
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    total = 0
    header_written = False

    with open(output_path, "w", newline="") as f_out:
        writer = csv.writer(f_out)

        for csv_path in csv_paths:
            with open(csv_path, "r") as f_in:
                reader = csv.reader(f_in)
                header = next(reader)
                if not header_written:
                    writer.writerow(header)
                    header_written = True
                for row in reader:
                    writer.writerow(row)
                    total += 1

    return total

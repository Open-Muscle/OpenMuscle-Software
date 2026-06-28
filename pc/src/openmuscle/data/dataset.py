"""Load captured data into pandas DataFrames for training and analysis."""

import pandas as pd
from pathlib import Path


def load_capture(csv_path: str) -> pd.DataFrame:
    """Load a capture CSV file into a DataFrame."""
    return pd.read_csv(csv_path)


def detect_columns(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Auto-detect sensor and label columns by naming convention.

    Supports both naming conventions:
        - New format: R0C0, R0C1, ..., label_0, label_1, ...
        - Legacy format: Sensor_0, Sensor_1, ..., Label_0, Label_1, ...

    Returns:
        (sensor_columns, label_columns)
    """
    sensor_cols = [c for c in df.columns
                   if (c.startswith("R") or c.startswith("Sensor_"))
                   and "Timestamp" not in c]
    label_cols = [c for c in df.columns
                  if (c.startswith("label_") or c.startswith("Label_"))
                  and "Timestamp" not in c]

    if not sensor_cols:
        raise ValueError("No sensor columns found (expected R*C* or Sensor_* prefix)")
    if not label_cols:
        raise ValueError("No label columns found (expected label_* or Label_* prefix)")

    return sensor_cols, label_cols


def is_bilateral_v2(df: pd.DataFrame) -> bool:
    """True if this is a schema-v2 long capture with BOTH left and right bands
    (a `role` column carrying left and right). Single-role v2 and v1 captures
    return False and load through the standard single-source path."""
    if "role" not in df.columns:
        return False
    roles = {str(r) for r in df["role"].unique()}
    return "left" in roles and "right" in roles


def pivot_bilateral(df: pd.DataFrame,
                    window_ms: int = 50) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pivot a v2 long capture (interleaved left/right source rows) into the wide
    bilateral training matrix (PROTOCOL.md 8.4, schema-v2 doc section 3).

    For each LEFT frame, pair the nearest RIGHT frame within `window_ms` by
    `ts_hub_ms`, concatenate features Left-then-Right (60 + 60 = 120 for two V4
    bands), and take labels from the left row (the labeler value matched nearest
    the group ts). Left frames with no right within the window are DROPPED
    (board #0072/#0073). Uses pandas merge_asof for the nearest temporal join,
    the static-DataFrame analogue of the streaming TemporalMatcher.

    Returns (X, y): X columns are `<feat>_L` then `<feat>_R`; y is the labels.
    """
    sensor_cols, label_cols = detect_columns(df)
    if "ts_hub_ms" not in df.columns:
        raise ValueError("pivot_bilateral needs a ts_hub_ms column (schema v2)")

    left = (df[df["role"] == "left"]
            .sort_values("ts_hub_ms")[["ts_hub_ms"] + sensor_cols + label_cols]
            .reset_index(drop=True))
    right = (df[df["role"] == "right"]
             .sort_values("ts_hub_ms")[["ts_hub_ms"] + sensor_cols]
             .reset_index(drop=True))
    if left.empty or right.empty:
        raise ValueError("pivot_bilateral needs both left and right rows")

    merged = pd.merge_asof(
        left, right, on="ts_hub_ms", direction="nearest",
        tolerance=window_ms, suffixes=("_L", "_R"),
    )
    # Drop left frames with no right match inside the window (unpaired groups).
    right_marker = sensor_cols[0] + "_R"
    merged = merged.dropna(subset=[right_marker]).reset_index(drop=True)

    X = merged[[c + "_L" for c in sensor_cols] + [c + "_R" for c in sensor_cols]]
    y = merged[label_cols]
    return X, y


def load_training_data(csv_path: str,
                       bilateral_window_ms: int = 50,
                       role: str = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load a capture CSV and split into features (X) and labels (y).

    Schema-v2 multi-role (bilateral) captures are pivoted to the wide
    Left||Right matrix. This is also the GUARD (board #0072/#0073): a bilateral
    long CSV must NEVER fall through to the single-source path, which would
    silently pool left + right rows into one role-agnostic 60-feature set.

    `role` (left/right) selects the SEPARATE-MODEL-PER-HAND path (Tory directive
    2026-06-27): keep only that role's rows, then train a normal single-arm model
    on its own 60 muscle features -> its own hand labels. Run twice (left, right)
    to get two independent models. This deliberately SKIPS the bilateral pivot --
    we are NOT concatenating L||R into one model; each hand gets its own.

    Returns:
        (X, y) DataFrames
    """
    df = load_capture(csv_path)
    if role:
        role = str(role).strip().lower()
        if "role" not in df.columns:
            raise ValueError(
                f"--role {role} requested but the CSV has no 'role' column "
                "(not a schema-v2 capture)")
        df = df[df["role"] == role].reset_index(drop=True)
        if df.empty:
            roles = sorted({str(r) for r in load_capture(csv_path)["role"].unique()})
            raise ValueError(
                f"No rows with role={role} in {csv_path} (found roles: {roles})")
        sensor_cols, label_cols = detect_columns(df)
        return df[sensor_cols], df[label_cols]
    if is_bilateral_v2(df):
        return pivot_bilateral(df, window_ms=bilateral_window_ms)
    sensor_cols, label_cols = detect_columns(df)
    X = df[sensor_cols]
    y = df[label_cols]
    return X, y

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


def load_training_data(csv_path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load a capture CSV and split into features (X) and labels (y).

    Returns:
        (X, y) DataFrames
    """
    df = load_capture(csv_path)
    sensor_cols, label_cols = detect_columns(df)
    X = df[sensor_cols]
    y = df[label_cols]
    return X, y

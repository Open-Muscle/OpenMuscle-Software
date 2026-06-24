"""Export predictions and ground truth to JSON for web visualization."""

import json
import numpy as np
import pandas as pd
from pathlib import Path

from openmuscle.data.dataset import load_training_data
from openmuscle.ml.registry import ModelRegistry


def export_predictions(model_path: str, data_path: str, output_path: str) -> dict:
    """Run a trained model on a capture CSV and export results as JSON.

    The JSON output contains sensor data, actual labels (ground truth),
    and model predictions, suitable for web visualization.

    Args:
        model_path: path to trained model .pkl file
        data_path: path to CSV with sensor + label columns
        output_path: path for output .json file

    Returns:
        dict with summary info (n_samples, metrics, etc.)
    """
    from sklearn.metrics import mean_squared_error, r2_score

    registry = ModelRegistry()
    model = registry.load(model_path)

    X, y = load_training_data(data_path)
    sensor_cols = list(X.columns)
    label_cols = list(y.columns)

    # Load full dataframe for timestamps
    df = pd.read_csv(data_path)
    timestamps = None
    for ts_col in ['Sensor_Timestamp', 'timestamp', 'time']:
        if ts_col in df.columns:
            timestamps = df[ts_col].tolist()
            break

    # Run predictions
    predictions = model.predict(X)
    if predictions.ndim == 1:
        predictions = predictions.reshape(-1, 1)

    actuals = y.values

    # Compute metrics
    mse = float(mean_squared_error(actuals, predictions))
    r2 = float(r2_score(actuals, predictions))

    # Per-label metrics
    per_label = {}
    for i, col in enumerate(label_cols):
        per_label[col] = {
            "mse": float(mean_squared_error(actuals[:, i], predictions[:, i])),
            "r2": float(r2_score(actuals[:, i], predictions[:, i])),
        }

    # Build output structure
    n_samples = len(X)
    output = {
        "metadata": {
            "model_path": model_path,
            "data_path": data_path,
            "n_samples": n_samples,
            "sensor_columns": sensor_cols,
            "label_columns": label_cols,
            "metrics": {
                "overall_mse": mse,
                "overall_r2": r2,
                "per_label": per_label,
            },
        },
        "sensor_data": {},
        "labels": {},
        "predictions": {},
    }

    if timestamps:
        output["timestamps"] = timestamps

    # Sensor values per channel
    for col in sensor_cols:
        output["sensor_data"][col] = X[col].tolist()

    # Actual labels per channel
    for i, col in enumerate(label_cols):
        output["labels"][col] = actuals[:, i].tolist()

    # Predicted labels per channel
    for i, col in enumerate(label_cols):
        output["predictions"][col] = predictions[:, i].tolist()

    # Write JSON
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Exported {n_samples} samples to {output_path}")
    print(f"  Overall MSE: {mse:.4f}  R^2: {r2:.4f}")
    for col, m in per_label.items():
        print(f"  {col}: MSE={m['mse']:.4f}  R^2={m['r2']:.4f}")

    return output["metadata"]

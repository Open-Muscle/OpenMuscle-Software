"""ML model training pipeline.

Refactored from TrainModel/Train_Model_From_Data.py.
"""

from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, r2_score

from openmuscle.data.dataset import load_training_data
from openmuscle.ml.registry import ModelRegistry


def train_model(data_path: str, model_type: str = "random_forest",
                output: str = None, test_split: float = 0.2,
                n_estimators: int = 100, role: str = None) -> tuple:
    """Train a model from a CSV capture file.

    Args:
        data_path: path to CSV file with sensor + label columns
        model_type: model type ("random_forest")
        output: explicit output path (overrides registry)
        test_split: fraction of data for testing
        n_estimators: number of trees for RandomForest
        role: left/right -> SEPARATE-MODEL-PER-HAND. Keep only that role's rows
            and train a single-arm model. Run twice for model_left + model_right.
            None = legacy behavior (bilateral pivot if both roles present).

    Returns:
        (model, metrics_dict)
    """
    X, y = load_training_data(data_path, role=role)
    sensor_cols = list(X.columns)
    label_cols = list(y.columns)
    if role:
        print(f"Role filter: {role} -> {len(X)} rows, single-arm model")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_split, random_state=42
    )

    if model_type == "random_forest":
        model = RandomForestRegressor(n_estimators=n_estimators, random_state=42)
    else:
        raise ValueError(f"Unknown model type: {model_type}")

    print(f"Training {model_type} on {len(X_train)} samples...")
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    metrics = {
        "mse": float(mean_squared_error(y_test, y_pred)),
        "r2": float(r2_score(y_test, y_pred)),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "n_features": len(sensor_cols),
        "n_labels": len(label_cols),
        "sensor_columns": sensor_cols,
        "label_columns": label_cols,
        "model_type": model_type,
        "n_estimators": n_estimators,
        "role": role,
    }

    registry = ModelRegistry()
    if output:
        import pickle
        from pathlib import Path
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        with open(output, "wb") as f:
            pickle.dump(model, f)
        print(f"Model saved to {output}")
    else:
        saved_path = registry.save(model, model_type, metrics)
        print(f"Model saved to {saved_path}")

    print(f"MSE: {metrics['mse']:.4f}  R2: {metrics['r2']:.4f}")
    return model, metrics

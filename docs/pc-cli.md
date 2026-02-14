# OpenMuscle CLI

The `openmuscle` command-line tool provides a unified interface for all PC-side operations.

## Installation

```bash
cd pc/
pip install -e .          # Basic install
pip install -e ".[dev]"   # With test/lint tools
pip install -e ".[all]"   # Everything including VisPy 3D viz
```

## Commands

### `openmuscle receive`

Listen for devices and display a live heatmap.

```bash
openmuscle receive                          # Default port 3141
openmuscle receive --port 3141 --save-dir data/raw/flexgrid
```

### `openmuscle record`

Record paired FlexGrid + LASK5 data to CSV with temporal matching.

```bash
openmuscle record -o data/raw/merged/session_01.csv
openmuscle record -o capture.csv --port 3141 --duration 60
```

### `openmuscle train`

Train an ML model from captured data.

```bash
openmuscle train data/raw/merged/session_01.csv
openmuscle train data.csv -o data/models/my_model.pkl --trees 200 --test-split 0.3
```

### `openmuscle predict`

Run real-time inference with live visualization.

```bash
openmuscle predict -m data/models/random_forest_20250101_120000/model.pkl
openmuscle predict -m my_model.pkl --port 3141
```

### `openmuscle simulate`

Send synthetic data for testing without hardware.

```bash
openmuscle simulate --device-type flexgrid
openmuscle simulate --device-type lask5 --target-ip 192.168.1.100
openmuscle simulate --replay data/raw/legacy/capture_45.txt
```

### `openmuscle models`

List all trained models in the registry.

```bash
openmuscle models
```

## Typical Workflow

```bash
# 1. Capture training data (FlexGrid + LASK5 paired)
openmuscle record -o data/raw/merged/training_session.csv

# 2. Train a model
openmuscle train data/raw/merged/training_session.csv

# 3. Run real-time predictions
openmuscle predict -m data/models/random_forest_*/model.pkl
```

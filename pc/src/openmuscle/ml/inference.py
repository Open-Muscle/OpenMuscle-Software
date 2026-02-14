"""Real-time inference engine.

Receives live UDP packets, runs model predictions, and optionally
displays results via matplotlib.
"""

import time
import numpy as np

from openmuscle.receiver.udp_listener import UDPListener
from openmuscle.receiver.matcher import TemporalMatcher
from openmuscle.ml.registry import ModelRegistry


def run_inference(model_path: str, port: int = 3141):
    """Run real-time inference with live visualization.

    Args:
        model_path: path to trained model .pkl file
        port: UDP port to listen on
    """
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    registry = ModelRegistry()
    model = registry.load(model_path)
    print(f"Model loaded from {model_path}")

    listener = UDPListener(port=port)
    listener.start()

    matcher = TemporalMatcher(window_s=0.100)

    # State for visualization
    max_points = 500
    label_data = [np.zeros(max_points) for _ in range(4)]
    pred_data = [np.zeros(max_points) for _ in range(4)]

    fig, axes = plt.subplots(2, 1, figsize=(10, 6))
    ax_sensor, ax_pred = axes

    # Sensor heatmap (4x16)
    sensor_mat = np.zeros((4, 16))
    im = ax_sensor.imshow(sensor_mat, cmap="plasma", vmin=0, vmax=4096, aspect="auto")
    ax_sensor.set_title("Sensor Data")
    plt.colorbar(im, ax=ax_sensor, fraction=0.046, pad=0.04)

    # Prediction time series
    pred_lines = []
    label_lines = []
    x = np.arange(max_points)
    colors = ["#FF0000", "#00FF00", "#0000FF", "#FFFF00"]
    dark_colors = ["#990000", "#009900", "#000099", "#999900"]
    for i in range(4):
        ln, = ax_pred.plot(x, label_data[i], color=colors[i], label=f"Label {i}")
        label_lines.append(ln)
        pn, = ax_pred.plot(x, pred_data[i], color=dark_colors[i],
                           linestyle="--", label=f"Pred {i}")
        pred_lines.append(pn)
    ax_pred.set_title("Labels vs Predictions")
    ax_pred.legend(loc="upper right", fontsize=7)

    stats = {"flex_cnt": 0, "label_cnt": 0, "last_report": time.time()}

    def update(_frame):
        updated = False
        while not listener.packet_queue.empty():
            pkt = listener.packet_queue.get()

            if pkt.device_type == "lask5":
                matcher.add_label(pkt)
                stats["label_cnt"] += 1
                values = pkt.data.get("values", [0, 0, 0, 0])
                for i in range(min(4, len(values))):
                    label_data[i] = np.roll(label_data[i], -1)
                    label_data[i][-1] = values[i]
                updated = True

            elif pkt.device_type == "flexgrid":
                stats["flex_cnt"] += 1
                flat = pkt.flat_sensor_values()
                if len(flat) == 64:
                    sensor_mat[:] = np.array(flat).reshape(16, 4).T

                    matched = matcher.match(pkt)
                    label_vals = matched.data.get("values", [0, 0, 0, 0]) if matched else [0] * 4

                    features = np.array(flat + label_vals[:4]).reshape(1, -1)
                    try:
                        prediction = model.predict(features[:, :len(flat)]).flatten()
                        for i in range(min(4, len(prediction))):
                            pred_data[i] = np.roll(pred_data[i], -1)
                            pred_data[i][-1] = prediction[i]
                    except Exception:
                        pass
                    updated = True

        if time.time() - stats["last_report"] >= 2:
            print(f"FlexGrid: {stats['flex_cnt']}  LASK5: {stats['label_cnt']}  "
                  f"Unpaired: {matcher.unpaired_count}")
            stats["last_report"] = time.time()

        if updated:
            im.set_array(sensor_mat)
            for i in range(4):
                label_lines[i].set_ydata(label_data[i])
                pred_lines[i].set_ydata(pred_data[i])
            all_vals = np.concatenate(label_data + pred_data)
            if np.any(all_vals != 0):
                ax_pred.set_ylim(np.min(all_vals) - 10, np.max(all_vals) + 10)

    anim = FuncAnimation(fig, update, interval=50, cache_frame_data=False)
    plt.tight_layout()

    try:
        plt.show()
    finally:
        listener.stop()

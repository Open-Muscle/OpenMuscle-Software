"""Matplotlib heatmap visualization for FlexGrid sensor data.

Refactored from FlexGrid_Receiver.py.
"""

import time
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from openmuscle.receiver.udp_listener import UDPListener


def run_heatmap(port: int = 3141, save_dir: str = None):
    """Display a live heatmap of FlexGrid pressure data.

    Args:
        port: UDP port to listen on
        save_dir: optional directory to save captures to CSV
    """
    from openmuscle.data.storage import CaptureWriter

    listener = UDPListener(port=port)
    listener.start()

    rows, cols = 4, 16
    pressure_mat = np.zeros((rows, cols))
    pkt_count = 0
    last_report = time.time()

    writer = None
    if save_dir:
        writer = CaptureWriter(
            output_path=f"{save_dir}/flexgrid_capture_{int(time.time())}.csv",
            matrix_rows=rows, matrix_cols=cols, label_count=0,
        )

    fig, ax = plt.subplots()
    im = ax.imshow(pressure_mat, cmap="plasma", vmin=0, vmax=4096, aspect="auto")
    ax.set_title("FlexGrid Pressure Sensor Matrix")
    plt.colorbar(im, ax=ax, shrink=0.5, aspect=10)

    def update(_frame):
        nonlocal pkt_count, last_report
        updated = False

        while not listener.packet_queue.empty():
            pkt = listener.packet_queue.get()
            if pkt.device_type != "flexgrid":
                continue

            matrix = pkt.data.get("matrix")
            if matrix and len(matrix) == cols:
                pressure_mat[:] = np.array(matrix).T
                pkt_count += 1
                updated = True

                if writer:
                    flat = pressure_mat.flatten().tolist()
                    writer.write_row(pkt.receive_time, flat, [])

        if time.time() - last_report > 1:
            print(f"Packets: {pkt_count}")
            last_report = time.time()

        if updated:
            im.set_array(pressure_mat)
            ax.set_title(f"FlexGrid Pressure Matrix - Packets: {pkt_count}")
            return [im]
        return []

    anim = FuncAnimation(fig, update, interval=50, cache_frame_data=False)

    try:
        plt.show()
    finally:
        listener.stop()
        if writer:
            writer.close()
            print(f"CSV saved to {writer.path}")

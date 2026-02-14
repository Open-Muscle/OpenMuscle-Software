"""OpenMuscle CLI - unified command-line interface.

Usage:
    openmuscle receive     Listen for devices and display live data
    openmuscle record      Record sensor data to CSV
    openmuscle train       Train an ML model from captured data
    openmuscle predict     Run real-time inference with visualization
    openmuscle simulate    Send synthetic or replayed sensor data
    openmuscle models      List trained models
"""

import click


@click.group()
@click.version_option(package_name="openmuscle")
def main():
    """OpenMuscle PC tools for sensor capture, training, and inference."""
    pass


@main.command()
@click.option("--port", default=3141, help="UDP port to listen on")
@click.option("--save-dir", default=None, help="Directory to save captures (optional)")
def receive(port, save_dir):
    """Listen for devices and display a live heatmap."""
    from openmuscle.viz.heatmap import run_heatmap
    run_heatmap(port=port, save_dir=save_dir)


@main.command()
@click.option("--port", default=3141, help="UDP port to listen on")
@click.option("--output", "-o", required=True, help="Output CSV path")
@click.option("--duration", "-d", default=0, type=int,
              help="Recording duration in seconds (0=until Ctrl+C)")
def record(port, output, duration):
    """Record paired sensor + label data to CSV."""
    import time
    from openmuscle.receiver.udp_listener import UDPListener
    from openmuscle.receiver.matcher import TemporalMatcher
    from openmuscle.data.storage import CaptureWriter

    listener = UDPListener(port=port)
    listener.start()
    matcher = TemporalMatcher(window_s=0.100)

    with CaptureWriter(output_path=output) as writer:
        print(f"Recording to {output} (Ctrl+C to stop)")
        start = time.time()
        last_report = start

        try:
            while True:
                if duration > 0 and time.time() - start >= duration:
                    break

                if not listener.packet_queue.empty():
                    pkt = listener.packet_queue.get()

                    if pkt.device_type == "lask5":
                        matcher.add_label(pkt)
                    elif pkt.device_type == "flexgrid":
                        flat = pkt.flat_sensor_values()
                        matched = matcher.match(pkt)
                        labels = matched.data.get("values", [0] * 4) if matched else [0] * 4
                        writer.write_row(pkt.receive_time, flat, labels[:4])

                if time.time() - last_report >= 1:
                    print(f"Rows: {writer.row_count}  "
                          f"Unpaired: {matcher.unpaired_count}")
                    last_report = time.time()

                time.sleep(0.001)
        except KeyboardInterrupt:
            pass

    listener.stop()
    print(f"\nSaved {writer.row_count} rows to {output}")


@main.command()
@click.argument("data_path")
@click.option("--model-type", default="random_forest", help="Model type to train")
@click.option("--output", "-o", default=None, help="Output model path (default: data/models/)")
@click.option("--test-split", default=0.2, type=float, help="Test set fraction")
@click.option("--trees", default=100, type=int, help="Number of trees (RandomForest)")
def train(data_path, model_type, output, test_split, trees):
    """Train an ML model from a captured CSV file."""
    from openmuscle.ml.training import train_model
    train_model(data_path=data_path, model_type=model_type,
                output=output, test_split=test_split, n_estimators=trees)


@main.command()
@click.option("--model", "-m", required=True, help="Path to trained model .pkl file")
@click.option("--port", default=3141, help="UDP port to listen on")
def predict(model, port):
    """Run real-time inference with live visualization."""
    from openmuscle.ml.inference import run_inference
    run_inference(model_path=model, port=port)


@main.command()
@click.option("--port", default=3141, help="UDP port to send to")
@click.option("--device-type", default="flexgrid",
              help="Device type to simulate (flexgrid, lask5)")
@click.option("--replay", type=click.Path(exists=True),
              help="Replay a capture file instead of generating synthetic data")
@click.option("--target-ip", default="127.0.0.1", help="Target IP address")
def simulate(port, device_type, replay, target_ip):
    """Send synthetic or replayed sensor data over UDP."""
    from openmuscle.simulate.transmitter import run_simulator
    run_simulator(port=port, device_type=device_type,
                  replay_file=replay, target_ip=target_ip)


@main.command()
def models():
    """List trained models in the registry."""
    from openmuscle.ml.registry import ModelRegistry
    registry = ModelRegistry()
    model_list = registry.list_models()
    if not model_list:
        print("No models found in data/models/")
        return
    for m in model_list:
        metrics = m.get("metrics", {})
        r2 = metrics.get("r2", "N/A")
        mse = metrics.get("mse", "N/A")
        print(f"  {m['name']}  created={m['created']}  "
              f"R2={r2}  MSE={mse}  path={m.get('path', '')}")

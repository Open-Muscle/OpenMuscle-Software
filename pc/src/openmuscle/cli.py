"""OpenMuscle CLI - unified command-line interface.

Usage:
    openmuscle receive     Listen for devices and display live data
    openmuscle record      Record sensor data to CSV
    openmuscle train       Train an ML model from captured data
    openmuscle predict     Run real-time inference with visualization
    openmuscle simulate    Send synthetic or replayed sensor data
    openmuscle models      List trained models
    openmuscle web         Launch the browser-based UI (live + record + captures)
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
@click.option("--host", default="0.0.0.0", help="HTTP bind address")
@click.option("--port", default=8000, help="HTTP port")
@click.option("--udp-port", default=3141, help="UDP port for sensor/label data frames")
@click.option("--announce-port", default=3140,
              help="UDP port for V4 discovery announce beacons (PROTOCOL.md v1.0 "
                   "splits beacons onto 3140; 3141 is data-only). The shared-3141 "
                   "type-tap stays as a fallback.")
@click.option("--captures-dir", default=None,
              help="Directory to save captures (default: data/raw/merged)")
@click.option("--model", "model_path", default=None, type=click.Path(),
              help="Path to a trained model.pkl. When set, FlexGrid frames are run "
                   "through the model and predictions appear in the LASK Inference panel.")
@click.option("--hand", "hand", default=None,
              help="Robot hand target as HOST or HOST:PORT (e.g. 10.0.0.55 or "
                   "10.0.0.55:3145). When set together with --model, inference "
                   "outputs are forwarded over UDP as 'PC,a1,a2,a3,a4,a5'. "
                   "Default port if omitted: 3145.")
@click.option("--ssl-certfile", default=None, type=click.Path(exists=True, dir_okay=False),
              help="Path to TLS cert (PEM). Required for WebXR over LAN -- Quest "
                   "Browser refuses hand-tracking on plain HTTP. Generate with "
                   "`mkcert <host-ip> <hostname>` and install the mkcert root CA "
                   "on the headset (Settings -> Security -> Install a certificate).")
@click.option("--ssl-keyfile", default=None, type=click.Path(exists=True, dir_okay=False),
              help="Path to TLS private key (PEM). Pair with --ssl-certfile.")
def web(host, port, udp_port, announce_port, captures_dir, model_path, hand, ssl_certfile, ssl_keyfile):
    """Launch the browser-based UI with live heatmap, recording, and captures."""
    from openmuscle.web.app import serve
    # mkcert produces a cert AND key; we need both or neither.
    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise click.BadParameter("--ssl-certfile and --ssl-keyfile must be used together")
    scheme = "https" if ssl_certfile else "http"
    click.echo(f"OpenMuscle web UI: {scheme}://{host if host != '0.0.0.0' else 'localhost'}:{port}")
    click.echo(f"Listening for devices on UDP {udp_port}")
    if ssl_certfile:
        click.echo(f"TLS: cert={ssl_certfile}  key={ssl_keyfile}")
        click.echo(f"WebXR URL for the Quest: {scheme}://<your-LAN-ip>:{port}/vr")
    if model_path:
        click.echo(f"Inference model: {model_path}")
    hand_target = None
    if hand:
        if ":" in hand:
            h, p = hand.rsplit(":", 1)
            try:
                hand_target = (h, int(p))
            except ValueError:
                raise click.BadParameter(f"--hand: invalid port in '{hand}'")
        else:
            hand_target = (hand, 3145)
        click.echo(f"Forwarding inference to robot hand at {hand_target[0]}:{hand_target[1]}")
    serve(host=host, port=port, udp_port=udp_port, captures_dir=captures_dir,
          model_path=model_path, hand_target=hand_target,
          ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile,
          announce_port=announce_port)


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
@click.argument("data_paths", nargs=-1, required=True,
                type=click.Path(exists=True, dir_okay=False))
@click.option("--model-type", default="random_forest", help="Model type to train")
@click.option("--output", "-o", default=None, help="Output model path (default: data/models/)")
@click.option("--test-split", default=0.2, type=float, help="Test set fraction")
@click.option("--trees", default=100, type=int, help="Number of trees (RandomForest)")
def train(data_paths, model_type, output, test_split, trees):
    """Train an ML model from one or more captured CSV files.

    \b
    Multiple captures are concatenated row-wise into a single training set
    before fitting -- run this when you have several short recordings and
    want them treated as one dataset:

        openmuscle train data/raw/merged/session_a.csv \\
                         data/raw/merged/session_b.csv \\
                         data/raw/merged/session_c.csv

    All CSVs must share the same schema (sensor + label columns).
    """
    import tempfile
    import os
    from openmuscle.ml.training import train_model
    from openmuscle.data.converter import combine_csvs

    if len(data_paths) == 1:
        # Single-CSV: train directly, no temp file dance.
        train_model(data_path=data_paths[0], model_type=model_type,
                    output=output, test_split=test_split, n_estimators=trees)
        return

    # Multiple captures: concatenate to a temp file, train, clean up.
    click.echo(f"Combining {len(data_paths)} captures...")
    fd, tmp_path = tempfile.mkstemp(prefix="om_combined_", suffix=".csv")
    os.close(fd)
    try:
        n_rows = combine_csvs(list(data_paths), tmp_path)
        click.echo(f"Combined into {n_rows} rows -> {tmp_path}")
        train_model(data_path=tmp_path, model_type=model_type,
                    output=output, test_split=test_split, n_estimators=trees)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


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
              type=click.Choice(["flexgrid", "lask5", "quest_hand", "combo"]),
              help="Device type to simulate. quest_hand streams synthetic "
                   "WebXR hand frames to the web server's /ws/quest; combo "
                   "adds a correlated flexgrid UDP device so record/train/"
                   "predict works end to end without hardware.")
@click.option("--replay", type=click.Path(exists=True),
              help="Replay a capture file instead of generating synthetic data")
@click.option("--target-ip", default="127.0.0.1", help="Target IP address")
@click.option("--web-port", default=8000,
              help="openmuscle web HTTP port (quest_hand/combo only)")
def simulate(port, device_type, replay, target_ip, web_port):
    """Send synthetic or replayed sensor data."""
    from openmuscle.simulate.transmitter import run_simulator
    run_simulator(port=port, device_type=device_type,
                  replay_file=replay, target_ip=target_ip, web_port=web_port)


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


@main.command()
@click.argument("input_path")
@click.option("--output", "-o", required=True, help="Output CSV path")
def convert(input_path, output):
    """Convert a legacy capture .txt file to matched CSV."""
    from openmuscle.data.converter import convert_legacy_capture
    rows = convert_legacy_capture(input_path, output)
    print(f"Converted {rows} matched rows to {output}")


@main.command()
@click.argument("csv_files", nargs=-1, required=True)
@click.option("--output", "-o", required=True, help="Output combined CSV path")
def combine(csv_files, output):
    """Combine multiple CSV files into one training set."""
    from openmuscle.data.converter import combine_csvs
    total = combine_csvs(list(csv_files), output)
    print(f"Combined {len(csv_files)} files -> {total} total rows in {output}")


@main.command()
@click.option("--model", "-m", required=True, help="Path to trained model .pkl file")
@click.option("--data", "-d", required=True, help="Path to capture CSV for prediction")
@click.option("--output", "-o", required=True, help="Output JSON path for website")
def export(model, data, output):
    """Export predictions + ground truth to JSON for web visualization."""
    from openmuscle.ml.export import export_predictions
    export_predictions(model_path=model, data_path=data, output_path=output)

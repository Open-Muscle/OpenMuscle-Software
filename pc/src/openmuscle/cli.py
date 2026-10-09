"""OpenMuscle CLI - unified command-line interface.

Usage:
    openmuscle receive     Listen for devices and display live data
    openmuscle record      Record sensor data to CSV
    openmuscle train       Train an ML model from captured data
    openmuscle predict     Run real-time inference with visualization
    openmuscle simulate    Send synthetic or replayed sensor data
    openmuscle models      List trained models
    openmuscle web         Launch the browser-based UI (live + record + captures)
    openmuscle assets      Download the hand tracker + 3D viewer for offline use

Everything is saved under one folder, ~/OpenMuscle (override with OPENMUSCLE_HOME
or `openmuscle web --data-dir`).
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


def _resolve_tls(certfile, keyfile):
    """Resolve the TLS cert/key for `openmuscle web`.

    Explicit --ssl-certfile/--ssl-keyfile win. Otherwise AUTO-DISCOVER a mkcert
    pair named vr-cert.pem + vr-key.pem from, in order:
      1. env vars OPENMUSCLE_SSL_CERTFILE / OPENMUSCLE_SSL_KEYFILE
      2. ~/.openmuscle/            (configure-once, works from any directory)
      3. the current directory     (where docs/vr-setup.md + the .bat put them)
    Returns (certfile, keyfile, source); source is None when nothing was found.
    """
    if certfile or keyfile:
        return certfile, keyfile, "explicit flags"
    import os
    home = os.path.join(os.path.expanduser("~"), ".openmuscle")
    candidates = [
        (os.environ.get("OPENMUSCLE_SSL_CERTFILE"),
         os.environ.get("OPENMUSCLE_SSL_KEYFILE"), "env OPENMUSCLE_SSL_*"),
        (os.path.join(home, "vr-cert.pem"),
         os.path.join(home, "vr-key.pem"), "~/.openmuscle/"),
        ("vr-cert.pem", "vr-key.pem", "current directory"),
    ]
    for cert, key, source in candidates:
        if cert and key and os.path.isfile(cert) and os.path.isfile(key):
            return cert, key, source
    return None, None, None


def _lan_ip():
    """Best-effort LAN IPv4 so the printed Quest URL is copy-pasteable."""
    try:
        from openmuscle.discovery.manager import default_lan_ip
        return default_lan_ip()
    except Exception:
        return "<your-LAN-ip>"


def _port_free(host, port):
    """True when a TCP listener could bind host:port right now."""
    import socket
    import sys
    bind_host = host if host not in ("", "localhost") else "127.0.0.1"
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if not sys.platform.startswith("win"):
            # Same as uvicorn: a lingering TIME_WAIT must not read as "busy".
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((bind_host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _pick_port(host, port, explicit):
    """Return a free HTTP port, never a traceback.

    A port the user asked for with --port must be free (clear one-line error
    otherwise). The default 8000 falls back to the next free port up to 8010 and
    says so, e.g. when another OpenMuscle window is already running.
    """
    if _port_free(host, port):
        return port
    if explicit:
        raise click.ClickException(
            f"Port {port} is already in use. Is OpenMuscle already running in "
            f"another window? Try http://localhost:{port} , or pick another "
            f"port with --port.")
    for alt in range(port + 1, port + 11):
        if _port_free(host, alt):
            click.echo(f"Port {port} is already in use (OpenMuscle may already be "
                       f"running in another window), so using port {alt} instead.")
            return alt
    raise click.ClickException(
        f"Ports {port} to {port + 10} are all in use. Close other programs or "
        f"pick a port with --port.")


def _echo_vr_help(port, tls_source, ssl_certfile, ssl_keyfile):
    """The Quest / WebXR / mkcert guidance. Only shown with --vr or --ssl-*."""
    if ssl_certfile:
        click.echo(f"TLS: cert={ssl_certfile}  key={ssl_keyfile}  (from {tls_source})")
        click.echo("     Your browser may warn about the certificate on this computer; "
                   "the headset trusts it once the mkcert root CA is installed there.")
        click.echo(f"WebXR URL for the Quest: https://{_lan_ip()}:{port}/vr?mode=ar&arm=both")
        return
    click.echo("")
    click.echo("  --vr: no TLS certificate found, so serving plain HTTP.")
    click.echo("      WebXR hand-tracking on the Quest over the LAN REQUIRES HTTPS.")
    click.echo("      One-time fix: make a mkcert pair named vr-cert.pem + vr-key.pem")
    click.echo("      and drop it in ~/.openmuscle/ (auto-loaded from anywhere). See")
    click.echo("      docs/vr-setup.md. Or set OPENMUSCLE_SSL_CERTFILE/KEYFILE, or pass")
    click.echo("      --ssl-certfile/--ssl-keyfile, or run from a folder holding the pair.")
    click.echo(f"      Tethered alt (no cert): adb reverse tcp:{port} tcp:{port}  ->  "
               f"http://localhost:{port}/vr")
    click.echo("")


@main.command()
@click.option("--host", default=None,
              help="HTTP bind address. Default 127.0.0.1 (this computer only). "
                   "Use --lan to share the page with other devices on your network.")
@click.option("--lan", is_flag=True, default=False,
              help="Listen on all network interfaces (0.0.0.0) so a phone or "
                   "headset on the same Wi-Fi can open the page. Anyone on that "
                   "network can reach it.")
@click.option("--vr", is_flag=True, default=False,
              help="Headset workflow: implies --lan, and serves https when a mkcert "
                   "pair (vr-cert.pem + vr-key.pem) is found in ~/.openmuscle/, "
                   "$OPENMUSCLE_SSL_CERTFILE/KEYFILE, or the current directory.")
@click.option("--port", default=8000, help="HTTP port (default 8000; if that one is busy "
                                           "the next free port is used)")
@click.option("--udp-port", default=3141, help="UDP port for sensor/label data frames")
@click.option("--announce-port", default=3140,
              help="UDP port for V4 discovery announce beacons (PROTOCOL.md v1.0 "
                   "splits beacons onto 3140; 3141 is data-only). The shared-3141 "
                   "type-tap stays as a fallback.")
@click.option("--data-dir", default=None, type=click.Path(file_okay=False),
              help="Folder for captures, sessions and models "
                   "(default ~/OpenMuscle; env OPENMUSCLE_HOME also works).")
@click.option("--captures-dir", default=None,
              help="Directory to save captures (default: <data folder>/captures). "
                   "Sessions are saved in a 'sessions' folder next to it.")
@click.option("--no-browser", is_flag=True, default=False,
              help="Do not open the page in your web browser automatically.")
@click.option("--verbose", is_flag=True, default=False,
              help="Show server request logs and low-level startup lines.")
@click.option("--model", "model_path", default=None, type=click.Path(),
              help="Path to a trained model.pkl. When set, FlexGrid frames are run "
                   "through the model and predictions appear in the LASK Inference panel.")
@click.option("--model-left", "model_left", default=None, type=click.Path(),
              help="SEPARATE-MODEL-PER-HAND: model.pkl for the LEFT band. A band "
                   "tagged 'left' in the Sources panel runs through this model; "
                   "pair with --model-right for true two-hand prediction (one model "
                   "per arm). Falls back to --model for any untagged band.")
@click.option("--model-right", "model_right", default=None, type=click.Path(),
              help="SEPARATE-MODEL-PER-HAND: model.pkl for the RIGHT band. See "
                   "--model-left.")
@click.option("--hand", "hand", default=None,
              help="Robot hand target as HOST or HOST:PORT (e.g. 10.0.0.55 or "
                   "10.0.0.55:3145). When set together with --model, inference "
                   "outputs are forwarded over UDP as 'PC,a1,a2,a3,a4,a5'. "
                   "Default port if omitted: 3145.")
@click.option("--ssl-certfile", default=None, type=click.Path(exists=True, dir_okay=False),
              help="Path to TLS cert (PEM); forces https. Needed for WebXR over LAN "
                   "(Quest Browser refuses hand-tracking on plain HTTP). Generate with "
                   "`mkcert <host-ip> <hostname>` and install the mkcert root CA "
                   "on the headset (Settings -> Security -> Install a certificate). "
                   "Or just use --vr if the pair is in ~/.openmuscle/.")
@click.option("--ssl-keyfile", default=None, type=click.Path(exists=True, dir_okay=False),
              help="Path to TLS private key (PEM). Pair with --ssl-certfile.")
@click.option("--debug", "debug", is_flag=True, default=False,
              help="DEBUG MODE: serve plain HTTP on all interfaces (no TLS), so the "
                   "PC, phone, and Quest can all connect with zero cert friction, and "
                   "unlock the verbose debug dashboard (hardware health, raw packets, "
                   "per-session eval). The server is LAN-open + unauthenticated in this "
                   "mode -- lab bench / demo use only, not the internet.")
@click.pass_context
def web(ctx, host, lan, vr, port, udp_port, announce_port, data_dir, captures_dir,
        no_browser, verbose, model_path, model_left, model_right, hand, ssl_certfile,
        ssl_keyfile, debug):
    """Launch the browser-based UI with live heatmap, recording, and captures.

    Opens http://localhost:8000 in your browser and saves everything under
    ~/OpenMuscle. Leave the window open; press Ctrl+C to stop.
    """
    from openmuscle import paths
    from openmuscle.web.app import serve

    if data_dir:
        paths.set_data_root(data_dir)
    paths.ensure_data_dirs()

    # Bind address: an explicit --host wins; --lan, --vr and --debug open the LAN;
    # otherwise this computer only.
    # Explicit --ssl-* also opens the LAN: that flag pair is the documented way to
    # serve the headset, which can only reach the PC over the network.
    if host is None:
        host = "0.0.0.0" if (lan or vr or debug or ssl_certfile or ssl_keyfile) else "127.0.0.1"

    # TLS is opt-in: explicit --ssl-* always forces https, --vr looks up the mkcert
    # pair, and a plain `openmuscle web` stays http whatever certs happen to exist.
    tls_source = None
    if ssl_certfile or ssl_keyfile:
        ssl_certfile, ssl_keyfile, tls_source = _resolve_tls(ssl_certfile, ssl_keyfile)
    elif vr:
        ssl_certfile, ssl_keyfile, tls_source = _resolve_tls(None, None)
    # mkcert produces a cert AND key; we need both or neither.
    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise click.BadParameter("--ssl-certfile and --ssl-keyfile must be used together")
    if debug:
        # Debug mode is deliberately plain-HTTP + all-interfaces so every device
        # (PC, phone, Quest) connects with no cert friction. Drop any resolved TLS.
        if ssl_certfile:
            click.echo("  DEBUG: ignoring the TLS cert -- serving plain HTTP so all "
                       "devices connect without the mkcert root CA.")
        ssl_certfile = ssl_keyfile = None
        tls_source = None
    vr_mode = bool(vr or ssl_certfile)

    port_explicit = ctx.get_parameter_source("port").name == "COMMANDLINE"
    port = _pick_port(host, port, port_explicit)

    scheme = "https" if ssl_certfile else "http"
    url = f"{scheme}://localhost:{port}"
    captures_path = paths.absolute(captures_dir) if captures_dir else paths.captures_dir()

    click.echo("OpenMuscle is running.")
    click.echo("")
    click.echo(f"  Open this address in your browser:  {url}"
               + ("" if no_browser else "   (opening it for you)"))
    click.echo(f"  Your data folder:                   {paths.data_root()}")
    click.echo(f"  Recordings are saved in:            {captures_path}")
    if host not in ("127.0.0.1", "localhost"):
        click.echo(f"  Other devices on your network:      {scheme}://{_lan_ip()}:{port}")
    click.echo("")
    click.echo("  Leave this window open while you work. Close it or press Ctrl+C to stop.")
    click.echo("  If your computer asks whether to allow OpenMuscle or Python through the")
    click.echo("  firewall, choose Allow (Private networks).")
    click.echo("")
    click.echo("  Waiting for bands... make sure the bands and this computer are on the same Wi-Fi.")
    hint = paths.legacy_hint()
    if hint:
        click.echo("")
        click.echo("  " + hint)
    click.echo("")
    if verbose:
        click.echo(f"Listening for devices on UDP {udp_port}")
    if vr_mode:
        _echo_vr_help(port, tls_source, ssl_certfile, ssl_keyfile)
    if model_path:
        click.echo(f"Inference model: {model_path}")
    if model_left:
        click.echo(f"Left-hand model:  {model_left}")
    if model_right:
        click.echo(f"Right-hand model: {model_right}")
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
    if debug:
        click.echo("")
        click.echo("  ============================================================")
        click.echo("  ==  DEBUG MODE: LAN-open + unauthenticated. Anyone on this  ")
        click.echo(f"  ==  network can reach http://{_lan_ip()}:{port}/ . Lab/demo only. ")
        click.echo("  ==  Verbose dashboard unlocked; file-manager reveal disabled.")
        click.echo("  ============================================================")
        click.echo("")

    def _device_line(_kind, message):
        click.echo(message)

    serve(host=host, port=port, udp_port=udp_port,
          captures_dir=str(captures_path) if captures_dir else None,
          model_path=model_path, model_left=model_left, model_right=model_right,
          hand_target=hand_target,
          ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile,
          announce_port=announce_port, debug=debug,
          open_browser_url=None if no_browser else url,
          verbose=verbose, on_device_event=_device_line)


@main.command()
@click.option("--port", default=3141, help="UDP port to listen on")
@click.option("--output", "-o", required=True, help="Output CSV path")
@click.option("--duration", "-d", default=0, type=int,
              help="Recording duration in seconds (0=until Ctrl+C)")
@click.option("--role", default="left",
              help="schema-v2 role tag for the sensor band: left / right / labeler")
def record(port, output, duration, role):
    """Record paired sensor + label data to a schema-v2 CSV.

    Writes the v2 layout (ts_hub_ms, role, device_id, R{r}C{c}.., label_*),
    matching the web recorder + the byte golden. Mirrors openmuscle web so every
    non-multi-band capture path produces one CSV format.
    """
    import time
    from openmuscle.receiver.udp_listener import UDPListener
    from openmuscle.receiver.matcher import TemporalMatcher
    from openmuscle.data.storage import CaptureWriter

    role = (role or "left").strip().lower()
    if role not in ("left", "right", "labeler"):
        raise click.BadParameter("--role must be left, right, or labeler")

    listener = UDPListener(port=port)
    listener.start()
    matcher = TemporalMatcher(window_s=0.100)

    # The writer is created lazily on the first FlexGrid frame so its header uses
    # the device's REAL matrix dims (a V4 band is 15x4, not the 16x4 default --
    # creating it up front produced a ragged header on V4) and the v2 row layout.
    writer = None
    print(f"Recording to {output} (role={role}, Ctrl+C to stop)")
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
                    matrix = pkt.data.get("matrix")
                    if not matrix:
                        continue
                    if writer is None:
                        writer = CaptureWriter(
                            output_path=output,
                            matrix_rows=len(matrix[0]), matrix_cols=len(matrix),
                            label_count=4, schema_version="v2")
                    flat = pkt.flat_sensor_values()
                    matched = matcher.match(pkt)
                    labels = matched.data.get("values", [0] * 4) if matched else [0] * 4
                    writer.write_row_v2(int(pkt.receive_time * 1000), role,
                                        pkt.device_id, flat, labels[:4])

            if time.time() - last_report >= 1:
                rc = writer.row_count if writer else 0
                print(f"Rows: {rc}  Unpaired: {matcher.unpaired_count}")
                last_report = time.time()

            time.sleep(0.001)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
        if writer is not None:
            writer.close()

    rc = writer.row_count if writer else 0
    print(f"\nSaved {rc} rows to {output}")


@main.command()
@click.argument("data_paths", nargs=-1, required=True,
                type=click.Path(exists=True, dir_okay=False))
@click.option("--model-type", default="random_forest", help="Model type to train")
@click.option("--output", "-o", default=None, help="Output model path (default: the models folder under ~/OpenMuscle)")
@click.option("--test-split", default=0.2, type=float,
              help="Fraction held out for the honest score: the last captures "
                   "(2+ files) or the chronological tail of a single file. The "
                   "saved model is fit on all rows.")
@click.option("--trees", default=100, type=int, help="Number of trees (RandomForest)")
@click.option("--role", default=None, type=click.Choice(["left", "right"]),
              help="SEPARATE-MODEL-PER-HAND: keep only this role's rows from a "
                   "two-hand schema-v2 capture and train a single-arm model. Run "
                   "twice (--role left -o model_left.pkl; --role right -o "
                   "model_right.pkl) to get one independent model per hand. Omit "
                   "for the legacy single/bilateral-pivot behavior.")
def train(data_paths, model_type, output, test_split, trees, role):
    """Train an ML model from one or more captured CSV files.

    \b
    Multiple captures are concatenated row-wise into a single training set
    before fitting -- run this when you have several short recordings and
    want them treated as one dataset:

        openmuscle train data/raw/merged/session_a.csv \\
                         data/raw/merged/session_b.csv \\
                         data/raw/merged/session_c.csv

    All CSVs must share the same schema (sensor + label columns). With two or
    more files the last ones are held out whole to score the model (give them
    oldest first); with one file the end of the recording is held out. The
    score is printed in plain words at the end.

    \b
    For two-hand captures, train one model per hand with --role:

        openmuscle train two_hand.csv --role left  -o model_left.pkl
        openmuscle train two_hand.csv --role right -o model_right.pkl
    """
    import tempfile
    import os
    from openmuscle.ml.training import train_model
    from openmuscle.ml.verdict import describe_result
    from openmuscle.data.converter import combine_csvs

    def _say(metrics, n_captures):
        v = describe_result(metrics, n_captures=n_captures, hand_side=role)
        click.echo("")
        click.echo(v["headline"])
        click.echo(v["detail"])
        click.echo("Next: " + v["next_step"])

    if len(data_paths) == 1:
        # Single-CSV: train directly, no temp file dance.
        _model, metrics = train_model(
            data_path=data_paths[0], model_type=model_type, output=output,
            test_split=test_split, n_estimators=trees, role=role)
        _say(metrics, 1)
        return

    # Multiple captures: concatenate to a temp file, train, clean up.
    click.echo(f"Combining {len(data_paths)} captures...")
    fd, tmp_path = tempfile.mkstemp(prefix="om_combined_", suffix=".csv")
    os.close(fd)
    try:
        n_rows = combine_csvs(list(data_paths), tmp_path,
                              capture_column="_capture")
        click.echo(f"Combined into {n_rows} rows -> {tmp_path}")
        _model, metrics = train_model(
            data_path=tmp_path, model_type=model_type, output=output,
            test_split=test_split, n_estimators=trees, role=role)
        _say(metrics, len(data_paths))
    except ValueError as e:
        # Mismatched-schema capture selection: fail loud + clean, not a traceback.
        raise click.ClickException(str(e))
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
              type=click.Choice(["flexgrid", "lask5", "quest_hand", "combo",
                                 "rehearsal"]),
              help="Device type to simulate. rehearsal is a whole V4 kit with no "
                   "hardware: 1 or 2 learnable 15x4 bands (announced on the "
                   "discovery port) plus a finger labeler driven by the same "
                   "curls, so record, train and predict give a sensible model. "
                   "quest_hand streams synthetic WebXR hand frames to the web "
                   "server's /ws/quest; combo adds a correlated flexgrid UDP "
                   "device. flexgrid and lask5 send random noise (plumbing "
                   "tests only, not learnable).")
@click.option("--replay", type=click.Path(exists=True),
              help="Replay a capture file instead of generating synthetic data")
@click.option("--target-ip", default="127.0.0.1", help="Target IP address")
@click.option("--web-port", default=8000,
              help="openmuscle web HTTP(S) port (quest_hand/combo/rehearsal)")
@click.option("--web-scheme", default="auto",
              type=click.Choice(["auto", "http", "https"]),
              help="How to reach the web server: auto probes the port for TLS, "
                   "so an HTTPS server (mkcert pair) gets wss:// automatically.")
@click.option("--announce-port", default=3140,
              help="UDP port for discovery beacons (rehearsal)")
@click.option("--bands", default=2, type=click.Choice(["1", "2"]),
              help="Rehearsal: number of pressure bands (left, or left + right)")
@click.option("--labeler", default="lask5",
              type=click.Choice(["lask5", "webcam", "quest"]),
              help="Rehearsal: what supplies the finger labels. lask5 is a "
                   "simulated LASK5 over UDP; webcam and quest stream a hand "
                   "to the web server.")
@click.option("--noise", default="default",
              help="Rehearsal: sensor noise, low / default / high or a number "
                   "(multiplier). default gives a clean learnable kit; high "
                   "buries the signal, so a model trained on it is weak.")
@click.option("--seed", default=1138, type=int,
              help="Rehearsal: random seed (same seed, same kit)")
@click.option("--speed", default=1.0, type=click.FloatRange(min=0, min_open=True),
              help="Rehearsal: run faster than real time (for tests)")
@click.option("--duration", default=None, type=float,
              help="Rehearsal: stop after this many seconds (default: until Ctrl+C)")
@click.option("--tag-roles/--no-tag-roles", default=True,
              help="Rehearsal: tag the bands left/right and the LASK5 as labeler "
                   "on the running web server (otherwise set them in Sources)")
def simulate(port, device_type, replay, target_ip, web_port, web_scheme,
             announce_port, bands, labeler, noise, seed, speed, duration,
             tag_roles):
    """Send synthetic or replayed sensor data.

    
    No hardware? Run a whole kit next to `openmuscle web`:

        openmuscle simulate --device-type rehearsal
    """
    from openmuscle.simulate.transmitter import run_simulator
    if device_type == "rehearsal":
        from openmuscle.simulate.rehearsal import parse_noise
        try:
            parse_noise(noise)
        except ValueError as e:
            raise click.BadParameter(str(e), param_hint="--noise")
    run_simulator(port=port, device_type=device_type,
                  replay_file=replay, target_ip=target_ip, web_port=web_port,
                  web_scheme=web_scheme, announce_port=announce_port,
                  bands=int(bands), labeler=labeler, noise=noise, seed=seed,
                  speed=speed, duration=duration, tag_roles=tag_roles)


@main.command()
def models():
    """List trained models in the registry."""
    from openmuscle.ml.registry import ModelRegistry
    registry = ModelRegistry()
    model_list = registry.list_models()
    if not model_list:
        print(f"No models found in {registry.base_dir}")
        return
    from openmuscle.ml.verdict import describe_result
    for m in model_list:
        metrics = m.get("metrics", {})
        v = describe_result(metrics, n_captures=len(m.get("captures") or []) or None,
                            hand_side=metrics.get("role"))
        r2 = metrics.get("r2", "N/A")
        mse = metrics.get("mse", "N/A")
        print(f"  {m['name']}  created={m['created']}  {v['headline']}  "
              f"(legacy R2={r2}  MSE={mse})  path={m.get('path', '')}")


@main.group()
def assets():
    """Offline copies of the webcam hand tracker and the 3D hand viewer.

    Without them the webcam page and the 3D viewer load from the internet.
    Fetch them once while online and they work with the network off.
    """


@assets.command("fetch")
@click.option("--data-dir", default=None, type=click.Path(file_okay=False),
              help="Data folder (default ~/OpenMuscle). Files go in its assets folder.")
@click.option("--with-nosimd", is_flag=True, default=False,
              help="Also fetch the tracker files for very old processors without "
                   "SIMD (about 9.5 MB more; almost nobody needs them).")
@click.option("--force", is_flag=True, default=False,
              help="Download again even if the files are already there and correct.")
def assets_fetch(data_dir, with_nosimd, force):
    """Download the pinned tracker and viewer files and verify their checksums."""
    from openmuscle import assets as om_assets
    from openmuscle import paths
    if data_dir:
        paths.set_data_root(data_dir)
    dest = paths.assets_dir()
    click.echo(f"Fetching offline browser files into {dest}")
    try:
        got = om_assets.fetch(include_optional=with_nosimd, force=force, say=click.echo)
    except om_assets.AssetError as e:
        raise click.ClickException(str(e))
    click.echo("")
    if got:
        click.echo(f"Done. {len(got)} file(s) downloaded and checked.")
    else:
        click.echo("Done. Everything was already in place and correct.")
    click.echo("Restart `openmuscle web` if it is running, then reload the page: the camera "
               "page should say 'tracker: ready (local)'.")


@assets.command("status")
@click.option("--data-dir", default=None, type=click.Path(file_okay=False),
              help="Data folder (default ~/OpenMuscle).")
@click.option("--hashes", is_flag=True, default=False,
              help="Print the sha256 of every installed file (for pinning values).")
def assets_status(data_dir, hashes):
    """Show which offline files are present and correct."""
    from openmuscle import assets as om_assets
    from openmuscle import paths
    if data_dir:
        paths.set_data_root(data_dir)
    click.echo(f"Offline browser files in {paths.assets_dir()}")
    for row in om_assets.status():
        a = row["asset"]
        state = "ok" if row["problem"] is None else row["problem"]
        extra = " (optional)" if a.optional else ""
        click.echo(f"  {a.rel}: {state}{extra}")
    for g, label in om_assets.GROUP_LABELS.items():
        click.echo(f"  {label}: " + ("ready" if om_assets.group_ready(g) else
                                     "not ready (run `openmuscle assets fetch`)"))
    if hashes:
        for h in om_assets.actual_hashes():
            click.echo(f"  {h['rel']}  sha256={h['sha256']}  md5={h['md5']}")


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
    """Combine multiple CSV files into one training set (must share a schema)."""
    from openmuscle.data.converter import combine_csvs
    try:
        total = combine_csvs(list(csv_files), output)
    except ValueError as e:
        raise click.ClickException(str(e))
    print(f"Combined {len(csv_files)} files -> {total} total rows in {output}")


@main.command()
@click.option("--model", "-m", required=True, help="Path to trained model .pkl file")
@click.option("--data", "-d", required=True, help="Path to capture CSV for prediction")
@click.option("--output", "-o", required=True, help="Output JSON path for website")
def export(model, data, output):
    """Export predictions + ground truth to JSON for web visualization."""
    from openmuscle.ml.export import export_predictions
    export_predictions(model_path=model, data_path=data, output_path=output)

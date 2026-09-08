"""Home Assistant options adapter; the receiver only reads the password via env."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

from .sdcard import SDFormatError, parse_backup, parse_log
from .upload_web import create_upload_server


def import_sd_options(options: dict, *, share_root=Path("/share"), log=print) -> dict:
    """Resolve opt-in SD candidates without rewriting HA's options file."""
    resolved = dict(options)
    if not options.get("sd_import", False):
        return resolved
    root = share_root.resolve()

    def read_shared(name):
        path = Path(name)
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if not path.is_relative_to(root):
            raise SDFormatError("SD files must be inside /share")
        with path.open("rb") as source:
            data = source.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise SDFormatError("SD file exceeds 4 MiB; use a smaller copy for analysis")
        return data

    def assign(key, candidate):
        existing = str(resolved.get(key, "")).strip().upper()
        if existing and existing != candidate:
            raise SDFormatError(f"Configured {key} conflicts with SD candidate; check the files or remove the import")
        resolved[key] = candidate
        log(f"SD import: {key} candidate {candidate}")

    backup_path = options.get("sd_backup_file", "")
    log_path = options.get("sd_log_file", "")
    if not backup_path and not log_path:
        raise SDFormatError("sd_import requires sd_backup_file and/or sd_log_file")
    if backup_path:
        backup = parse_backup(read_shared(backup_path))
        assign("interface_id", backup["interface_id_candidate"])
        ids = [item["device_id"] for item in backup["thermostat_candidates"]]
        log("SD import: thermostat candidates " + ", ".join(ids))
    if log_path:
        parsed = parse_log(read_shared(log_path))
        if parsed["invalid_body_records"] or not parsed["valid_body_records"]:
            raise SDFormatError("SD log has invalid or missing body records; inspect it with the sdcard CLI")
        assign("controller_id", parsed["controller_id_candidate"])
    if not resolved.get("controller_id", "").strip():
        raise SDFormatError("Backup has no controller ID; provide sd_log_file or controller_id")
    log("SD import: candidate IDs used for receive-only operation. Verify received devices; file pairing and header integrity are unverified.")
    return resolved


def resolve_app_options(
    options: dict,
    *,
    share_root=Path("/share"),
    upload_root=Path("/data/sd_import"),
    log=print,
) -> dict:
    """Prefer validated Ingress uploads, otherwise use configured SD import."""
    backup = upload_root / "U_BACKUP.TXT"
    sd_log = upload_root / "U_LOGA.TXT"
    if backup.is_file() or sd_log.is_file():
        uploaded = dict(options)
        uploaded["sd_import"] = True
        uploaded["sd_backup_file"] = backup.name if backup.is_file() else ""
        uploaded["sd_log_file"] = sd_log.name if sd_log.is_file() else ""
        log("SD import: using files uploaded through Home Assistant")
        return import_sd_options(uploaded, share_root=upload_root, log=log)
    return import_sd_options(options, share_root=share_root, log=log)


def receiver_command(options: dict, environ: dict) -> tuple[list[str], dict]:
    env = dict(environ)
    mapping = {
        "mqtt_host": "UPONOR_MQTT_HOST", "mqtt_port": "UPONOR_MQTT_PORT",
        "mqtt_username": "UPONOR_MQTT_USERNAME", "mqtt_password": "UPONOR_MQTT_PASSWORD",
        "topic_prefix": "UPONOR_MQTT_TOPIC_PREFIX",
        "discovery_prefix": "UPONOR_MQTT_DISCOVERY_PREFIX",
        "stale_after": "UPONOR_MQTT_STALE_AFTER",
        "controller_id": "UPONOR_CONTROLLER_ID",
        "interface_id": "UPONOR_INTERFACE_ID",
    }
    for key, name in mapping.items():
        if key in options:
            env[name] = str(options[key])
    command = [sys.executable, "-m", "uponor_smatrix_wave_x165", "--input", "rtl_sdr", "--mqtt", "--retry-rf"]
    for key in ("device", "frequency", "sample_rate", "ppm", "workers", "threshold_db", "debug_device"):
        if key in options and options[key] != "":
            command.extend(["--" + key.replace("_", "-"), str(options[key])])
    return command, env


class AppRuntime:
    """Keep Ingress available while starting and restarting the RF receiver."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.reload_event = threading.Event()
        self._status = "Starting …"
        self._lock = threading.Lock()

    def set_status(self, value: str) -> None:
        with self._lock:
            self._status = value

    def status(self) -> str:
        with self._lock:
            return self._status

    def reload(self) -> None:
        self.reload_event.set()


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main():
    data_root = Path(os.getenv("UPONOR_APP_DATA", "/data"))
    share_root = Path(os.getenv("UPONOR_SHARE_ROOT", "/share"))
    options_file = data_root / "options.json"
    upload_root = data_root / "sd_import"
    runtime = AppRuntime()
    allowed_ip = os.getenv("UPONOR_INGRESS_ALLOWED_IP", "172.30.32.2")
    port = int(os.getenv("UPONOR_INGRESS_PORT", "8099"))
    server = create_upload_server(
        upload_root,
        status=runtime.status,
        on_change=runtime.reload,
        port=port,
        allowed_ip=allowed_ip,
    )
    server_thread = threading.Thread(target=server.serve_forever, name="ingress", daemon=True)
    server_thread.start()
    print(f"Uponor setup page listening for Home Assistant Ingress on port {port}", flush=True)

    def request_stop(_signum, _frame):
        runtime.stop_event.set()
        runtime.reload_event.set()

    previous_handlers = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, request_stop)

    process = None
    try:
        while not runtime.stop_event.is_set():
            runtime.reload_event.clear()
            try:
                options = json.loads(options_file.read_text()) if options_file.exists() else {}
                options = resolve_app_options(
                    options,
                    share_root=share_root,
                    upload_root=upload_root,
                    log=lambda message: print(message, flush=True),
                )
                if not str(options.get("controller_id", "")).strip():
                    raise SDFormatError("X-165 ID is missing; upload U_LOGA.TXT or configure controller_id")
                if not str(options.get("mqtt_host", "")).strip():
                    raise SDFormatError("MQTT host is missing from the app configuration")
            except (OSError, ValueError) as exc:
                message = f"Waiting for configuration: {exc}"
                runtime.set_status(message)
                print(message, flush=True)
                runtime.reload_event.wait()
                continue

            command, env = receiver_command(options, os.environ)
            runtime.set_status(
                f"Receiver running for X-165 {str(options['controller_id']).upper()}"
            )
            print("Starting receive-only RF process", flush=True)
            process = subprocess.Popen(command + sys.argv[1:], env=env)
            while (
                process.poll() is None
                and not runtime.stop_event.is_set()
                and not runtime.reload_event.wait(0.5)
            ):
                pass
            if process.poll() is None:
                _stop_process(process)
            exit_code = process.returncode
            process = None
            if runtime.stop_event.is_set():
                break
            if runtime.reload_event.is_set():
                runtime.set_status("Loading uploaded files …")
                continue
            runtime.set_status(f"Receiver exited with code {exit_code}; retrying")
            print(runtime.status(), file=sys.stderr, flush=True)
            runtime.reload_event.wait(5)
    finally:
        if process is not None:
            _stop_process(process)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

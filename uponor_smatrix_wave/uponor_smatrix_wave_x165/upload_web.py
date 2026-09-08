"""Small Home Assistant Ingress UI for validated X-165 SD-file uploads."""
from __future__ import annotations

from email.parser import BytesParser
from email.policy import default
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import secrets

from .sdcard import SDFormatError, parse_backup, parse_log


MAX_FILE_SIZE = 4 * 1024 * 1024
MAX_REQUEST_SIZE = 2 * MAX_FILE_SIZE + 64 * 1024
UPLOAD_NAMES = {
    "backup_file": ("U_BACKUP.TXT", parse_backup),
    "log_file": ("U_LOGA.TXT", parse_log),
}


class UploadError(ValueError):
    """The HTTP upload is missing, malformed, or unsupported."""


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, bytes], dict[str, str]]:
    """Extract file and ordinary fields from a multipart request."""
    if not content_type.lower().startswith("multipart/form-data"):
        raise UploadError("The form must use multipart/form-data.")
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii", "strict")
        + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    if not message.is_multipart():
        raise UploadError("The upload has no valid multipart boundary.")
    files: dict[str, bytes] = {}
    fields: dict[str, str] = {}
    for part in message.iter_parts():
        field = part.get_param("name", header="content-disposition")
        if not field:
            continue
        payload = part.get_payload(decode=True)
        if part.get_filename():
            if field not in UPLOAD_NAMES:
                continue
            if field in files:
                raise UploadError("The same file type was submitted more than once.")
            if not payload:
                raise UploadError(f"{UPLOAD_NAMES[field][0]} is empty.")
            if len(payload) > MAX_FILE_SIZE:
                raise UploadError(f"{UPLOAD_NAMES[field][0]} exceeds 4 MiB.")
            files[field] = payload
        else:
            try:
                fields[field] = payload.decode(part.get_content_charset() or "utf-8")
            except UnicodeError as exc:
                raise UploadError("A form field has invalid text encoding.") from exc
    return files, fields


def parse_upload(content_type: str, body: bytes) -> dict[str, bytes]:
    """Extract supported binary file fields from multipart data."""
    files, _fields = _parse_multipart(content_type, body)
    if not files:
        raise UploadError("Select U_BACKUP.TXT and/or U_LOGA.TXT.")
    return files


def validate_upload(files: dict[str, bytes]) -> dict:
    """Parse every supplied file before any persistent file is replaced."""
    parsed = {}
    for field, data in files.items():
        filename, parser = UPLOAD_NAMES[field]
        try:
            result = parser(data)
        except SDFormatError as exc:
            raise UploadError(f"{filename} is not recognized: {exc}") from exc
        if field == "log_file" and (
            result["invalid_body_records"] or not result["valid_body_records"]
        ):
            raise UploadError(
                "U_LOGA.TXT contains damaged or missing log records."
            )
        parsed[field] = result
    return parsed


def save_upload(upload_root: Path, files: dict[str, bytes]) -> None:
    """Atomically save validated files in the app's persistent data volume."""
    upload_root.mkdir(parents=True, exist_ok=True)
    for field, data in files.items():
        filename, _ = UPLOAD_NAMES[field]
        target = upload_root / filename
        temporary = upload_root / (filename + ".tmp")
        temporary.write_bytes(data)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(target)


def inspect_saved(upload_root: Path) -> dict:
    """Return parsed candidates and errors for files already in /data."""
    result = {}
    for field, (filename, parser) in UPLOAD_NAMES.items():
        path = upload_root / filename
        if not path.is_file():
            continue
        try:
            result[field] = parser(path.read_bytes())
        except (OSError, SDFormatError) as exc:
            result[field] = {"error": str(exc)}
    return result


def _summary(saved: dict) -> str:
    rows = []
    backup = saved.get("backup_file")
    if backup and "error" not in backup:
        thermostats = ", ".join(
            item["device_id"] for item in backup["thermostat_candidates"]
        ) or "none"
        rows.extend(
            (
                f"<li><strong>I-167:</strong> {escape(backup['interface_id_candidate'])}</li>",
                f"<li><strong>Thermostats:</strong> {escape(thermostats)}</li>",
            )
        )
    log = saved.get("log_file")
    if log and "error" not in log:
        rows.append(
            f"<li><strong>X-165:</strong> {escape(log['controller_id_candidate'])}</li>"
        )
    for field, value in saved.items():
        if "error" in value:
            rows.append(
                f"<li><strong>{escape(UPLOAD_NAMES[field][0])}:</strong> "
                f"{escape(value['error'])}</li>"
            )
    return "<ul>" + "".join(rows) + "</ul>" if rows else "<p>No SD-card files are uploaded.</p>"


def render_page(upload_root: Path, status: str, csrf_token: str, notice: str = "") -> bytes:
    saved = inspect_saved(upload_root)
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Uponor X-165 setup</title>
<style>
body{{font:16px system-ui,sans-serif;margin:0;background:#f5f7fa;color:#18212b}}
main{{max-width:720px;margin:auto;padding:24px}}section{{background:white;padding:20px;margin:0 0 16px;border-radius:12px}}
h1{{font-size:1.6rem}}label{{display:block;font-weight:600;margin-top:16px}}input[type=file]{{display:block;margin-top:6px;width:100%}}
button,.button{{display:inline-block;margin-top:20px;padding:10px 16px;border:0;border-radius:6px;background:#03a9f4;color:white;font-weight:700;text-decoration:none;cursor:pointer}}
.secondary{{background:#66717c}}.notice{{padding:12px;background:#e7f5ff;border-radius:6px}}code{{overflow-wrap:anywhere}}
</style></head><body><main>
<a class="button secondary" href="/hassio/dashboard" target="_top">← Back to Home Assistant</a>
<h1>Uponor Smatrix Wave X-165</h1>{notice_html}
<section><h2>Status</h2><p>{escape(status)}</p>{_summary(saved)}</section>
<section><h2>Upload files from the X-165</h2>
<p>Select copies of the files from the X-165 microSD card. They are validated and stored only in the app's private data directory.</p>
<form method="post" enctype="multipart/form-data">
<input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
<label>U_BACKUP.TXT<input type="file" name="backup_file"></label>
<label>U_LOGA.TXT<input type="file" name="log_file"></label>
<button type="submit" name="action" value="upload">Validate and use</button>
</form></section>
<section><h2>Remove imported files</h2>
<form method="post"><input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
<input type="hidden" name="action" value="clear"><button class="secondary" type="submit">Remove</button></form>
</section></main></body></html>"""
    return html.encode("utf-8")


def create_upload_server(
    upload_root: Path,
    *,
    status,
    on_change,
    host: str = "0.0.0.0",
    port: int = 8099,
    allowed_ip: str = "172.30.32.2",
) -> ThreadingHTTPServer:
    """Create an Ingress-only HTTP server without starting its thread."""
    csrf_token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def _allowed(self) -> bool:
            return self.client_address[0] == allowed_ip

        def _reply(self, code: int, notice: str = "") -> None:
            content = render_page(upload_root, status(), csrf_token, notice)
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            if not self._allowed():
                self.send_error(403)
                return
            self._reply(200)

        def do_POST(self) -> None:
            if not self._allowed():
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_REQUEST_SIZE:
                    raise UploadError("The upload is empty or too large.")
                body = self.rfile.read(length)
                content_type = self.headers.get("Content-Type", "")
                if content_type.startswith("application/x-www-form-urlencoded"):
                    from urllib.parse import parse_qs
                    form = parse_qs(body.decode("utf-8", "strict"))
                    if form.get("csrf_token") != [csrf_token] or form.get("action") != ["clear"]:
                        raise UploadError("Invalid form request.")
                    for filename, _ in UPLOAD_NAMES.values():
                        (upload_root / filename).unlink(missing_ok=True)
                    on_change()
                    self._reply(200, "The imported files were removed.")
                    return
                files, fields = _parse_multipart(content_type, body)
                if not files:
                    raise UploadError("Select U_BACKUP.TXT and/or U_LOGA.TXT.")
                if fields.get("csrf_token") != csrf_token:
                    raise UploadError("Invalid form request.")
                validate_upload(files)
                save_upload(upload_root, files)
                on_change()
                self._reply(200, "The files were validated and saved. The receiver restarts automatically.")
            except (OSError, ValueError) as exc:
                self._reply(400, str(exc))

        def log_message(self, fmt: str, *args) -> None:
            print("Ingress: " + (fmt % args), flush=True)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server

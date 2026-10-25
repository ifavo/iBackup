"""Authenticated loopback service. All backup operations are read-only."""
import hmac
import json
import mimetypes
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .backup import Backup, BackupError
from .content import PHOTO_TYPES, VIDEO_TYPES
from .recordings import is_recording
from .session import Store

STATIC = Path(__file__).parent / "static"
CHUNK = 1024 * 1024


def byte_range(header, size):
    if not header:
        return 0, size
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not size:
        raise ValueError()
    first, last = match.groups()
    if not first and not last:
        raise ValueError()
    if not first:
        length = int(last)
        if length <= 0:
            raise ValueError()
        return max(0, size - length), size
    start = int(first)
    end = min(size, int(last) + 1) if last else size
    if start >= size or end <= start:
        raise ValueError()
    return start, end


def create_app(roots=None, token=None, port=8765):
    token = token or secrets.token_urlsafe(32)
    store = Store(roots)
    host = f"127.0.0.1:{port}"
    origin = f"http://{host}"
    cookie_name = f"ibackup_session_{port}"

    @asynccontextmanager
    async def lifespan(app):
        yield
        with store.mutex:
            store.lock()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def protect(request, call_next):
        if request.headers.get("host") != host or request.headers.get("origin", origin) != origin:
            return JSONResponse({"error": "Dieser Zugriff ist nur lokal erlaubt."}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "Öffne die App über das Startskript."}, status_code=403)
        if request.method not in {"GET", "HEAD"} and request.headers.get("x-ibackup") != "1":
            return JSONResponse({"error": "Öffne die App über das Startskript."}, status_code=403)
        if request.url.path.startswith("/api/") and request.url.path != "/api/session":
            if not hmac.compare_digest(request.cookies.get(cookie_name, ""), token):
                return JSONResponse({"error": "Starte iBackup mit dem Startskript, um diese Sitzung zu öffnen.", "code": "session_required"}, status_code=401)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        })
        return response

    async def body(request):
        data = await request.body()
        if len(data) > 16384:
            raise BackupError("Diese Eingabe ist zu lang.")
        try:
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise BackupError("Die Eingabe konnte nicht gelesen werden.") from None

    @app.exception_handler(BackupError)
    async def backup_error(request, error):
        status = 423 if error.code == "locked" else 404 if error.code == "not_found" else 422
        return JSONResponse({"error": str(error), "code": error.code}, status_code=status)

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return JSONResponse({"error": str(error), "code": "invalid_input"}, status_code=422)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, error):
        return JSONResponse({"error": "Diese Datenbank-Version konnte nicht gelesen werden.", "code": "unsupported_schema"}, status_code=422)

    @app.exception_handler(OSError)
    async def io_error(request, error):
        return JSONResponse({"error": "Der Backupordner ist nicht lesbar. Prüfe Pfad und Leserechte.", "code": "read_error"}, status_code=422)

    @app.post("/api/session")
    async def session(request: Request):
        data = await body(request)
        candidate = data.get("token", "")
        if not isinstance(candidate, str) or not hmac.compare_digest(candidate, token):
            return JSONResponse({"error": "Dieser Startlink ist nicht mehr gültig. Starte die App erneut."}, status_code=401)
        response = JSONResponse({"ok": True})
        response.set_cookie(cookie_name, token, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/api/backups")
    def backups():
        with store.mutex:
            return store.discover()

    @app.post("/api/inspect")
    async def inspect(request: Request):
        data = await body(request)
        path = data.get("path")
        if not isinstance(path, str) or not path.strip():
            raise BackupError("Du brauchst den Pfad zum Ordner mit Info.plist und Manifest.mbdb.")
        with store.mutex:
            backup = Backup(path)
            item = backup.metadata()
            store.known[item["id"]] = item
            return item

    @app.post("/api/open")
    async def open_backup(request: Request):
        data = await body(request)
        password = data.get("password", "")
        if not isinstance(password, str) or len(password) > 1024:
            raise BackupError("Das Passwort muss Text mit höchstens 1.024 Zeichen sein.")
        with store.mutex:
            return store.open(str(data.get("id")), password)

    @app.post("/api/select")
    async def select_backup(request: Request):
        data = await body(request)
        with store.mutex:
            return store.select(str(data.get("id")))

    @app.get("/api/status")
    def status():
        with store.mutex:
            return store.status()

    @app.post("/api/lock")
    def lock():
        with store.mutex:
            store.lock()
            return {"ok": True}

    @app.get("/api/diagnostics")
    def diagnostics():
        """Schema and aggregate status only; never returns personal content."""
        with store.mutex:
            library = store.require_library()
            schemas = []
            for record in library.backup.files.values():
                if not record.path.endswith(("NoteStore.sqlite", "Recordings.db", "CloudRecordings.db")):
                    continue
                try:
                    db = library._database(record.path, record.domain)
                    tables = {}
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 80"):
                        table = row[0].replace('"', '""')
                        tables[row[0]] = [col[1] for col in db.execute(f'PRAGMA table_info("{table}")')]
                    schemas.append({"domain": record.domain, "file": record.path, "tables": tables})
                except BackupError as error:
                    schemas.append({"file": record.path, "error": str(error)})
            totals = {}
            for section in ("photos", "recordings", "notes", "contacts", "conversations"):
                try:
                    items = getattr(library, section)(include_deleted=True)
                    totals[section] = {"total": len(items), "deleted": sum(bool(i.get('deleted')) for i in items),
                                       "partial": sum(bool(i.get('notice')) for i in items),
                                       "protected": sum(bool(i.get('protected')) for i in items),
                                       "unindexed": sum(bool(i.get('unindexed')) for i in items)}
                except (BackupError, sqlite3.Error) as error:
                    totals[section] = {"error": str(error)}
            return {"schemas": schemas, "totals": totals}

    @app.get("/api/{section}")
    def content(section: str, history: bool = False):
        if section not in {"photos", "contacts", "notes", "conversations", "recordings"}:
            raise BackupError("Diese Ansicht gibt es nicht.", "not_found")
        with store.mutex:
            return store.bundle(section, history)

    @app.get("/api/messages/{chat_id}")
    def messages(chat_id: int, offset: int = 0, source: str | None = None, history: bool = False):
        with store.mutex:
            return store.messages(chat_id, source, history, offset)

    @app.get("/api/media/{file_id}")
    def media(file_id: str, request: Request, generation: int, source: str | None = None):
        with store.mutex:
            store.require_library()
            if generation != store.generation:
                return JSONResponse({"error": "Diese Ansicht ist abgelaufen. Öffne das Foto erneut."}, status_code=409)
            backup = store.require_library(source).backup
            record = backup.record(file_id)
            extension = Path(record.path).suffix.lower()
            photo = extension in PHOTO_TYPES | VIDEO_TYPES and record.domain == "CameraRollDomain" and record.path.startswith("Media/DCIM/")
            if not photo and not is_recording(record):
                raise BackupError("Diese Datei ist kein Foto, Video oder Sprachmemo aus dem Backup.")
            try:
                start, end = byte_range(request.headers.get("range"), record.size)
            except ValueError:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{record.size}"})
            first = backup.read(file_id, start, min(end, start + CHUNK))

        def stream():
            yield first
            position = start + len(first)
            while position < end:
                with store.mutex:
                    if generation != store.generation or not backup.unlocked:
                        return
                    chunk = backup.read(file_id, position, min(end, position + CHUNK))
                yield chunk
                position += len(chunk)

        headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start)}
        status_code = 206 if request.headers.get("range") else 200
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start}-{end - 1}/{record.size}"
        media_type = mimetypes.guess_type(record.path)[0] or "application/octet-stream"
        return StreamingResponse(stream(), status_code=status_code, media_type=media_type, headers=headers)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/{filename}")
    def asset(filename: str):
        if filename not in {"style.css", "app.js", "favicon.svg"}:
            return Response(status_code=404)
        return FileResponse(STATIC / filename)

    return app

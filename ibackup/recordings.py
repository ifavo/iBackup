"""Apple Voice Memos files with optional metadata; unindexed is not deleted."""
from pathlib import PurePosixPath
from urllib.parse import unquote

from .backup import BackupError
from .content import AUDIO_TYPES, apple_date, columns, deleted_row, select_all

VOICE_DOMAINS = {"AppDomain-com.apple.VoiceMemos", "AppDomainGroup-group.com.apple.VoiceMemos.shared"}


def is_recording(record):
    return PurePosixPath(record.path).suffix.lower() in AUDIO_TYPES and (
        record.domain == "MediaDomain" and record.path.startswith("Media/Recordings/")
        or record.domain in VOICE_DOMAINS and "Recordings/" in record.path)


def recording_database(record):
    return (record.domain == "MediaDomain" and record.path == "Media/Recordings/Recordings.db"
            or record.domain in VOICE_DOMAINS and record.path.endswith(("Recordings.db", "CloudRecordings.db")))


def first(row, names):
    return next((row[name] for name in names if row.get(name) is not None), None)


def read_recordings(library, include_deleted=False):
    library.backup.require_unlocked()
    files = [r for r in library.backup.files.values() if is_recording(r)]
    entries, notices = [], []
    for record in library.backup.files.values():
        if not recording_database(record):
            continue
        try:
            db = library._database(record.path, record.domain)
            tables = [name for name in ("recordings", "ZRECORDING", "ZCLOUDRECORDING") if columns(db, name)]
            for table in tables:
                entries.extend((record.domain, row) for row in select_all(db, table))
            if not tables:
                notices.append("Die Aufnahmen sind vorhanden; ihre Datenbank-Version ist noch unbekannt.")
        except BackupError as error:
            notices.append(str(error))
    result = []
    for record in files:
        name = PurePosixPath(record.path).name
        candidates = []
        for domain, row in entries:
            if domain != record.domain:
                continue
            stored = first(row, ("path", "zpath", "recording_path", "filename", "zfilename", "url"))
            if not isinstance(stored, str):
                continue
            stored = unquote(stored).removeprefix("file://")
            if domain == "MediaDomain":
                for prefix in ("/var/mobile/", "/private/var/mobile/"):
                    if stored.startswith(prefix + "Media/Recordings/"):
                        stored = stored.removeprefix(prefix)
                        break
            # A basename match is accepted only when unique in this Apple domain.
            matches = [f for f in files if f.domain == domain and
                       (f.path == stored or f.path.endswith('/' + stored.lstrip('/')) or
                        '/' not in stored and PurePosixPath(f.path).name == stored)]
            if len(matches) == 1 and matches[0].id == record.id:
                candidates.append(row)
        row = candidates[0] if len(candidates) == 1 else {}
        deleted = deleted_row(row)
        if deleted and not include_deleted:
            continue
        duration = first(row, ("duration", "zduration"))
        result.append({"id": record.id, "key": row.get("zuniqueid") or record.id,
                       "name": first(row, ("title", "zcustomlabel", "label", "zlabel", "name")) or name,
                       "filename": name, "deleted": deleted, "unindexed": not row,
                       "duration": duration if isinstance(duration, (int, float)) and duration >= 0 else None,
                       "date": apple_date(first(row, ("date", "zdate", "zcreationdate", "recording_date", "creation_date"))),
                       "size": record.size})
    library.notices["recordings"] = " ".join(notices)
    return sorted(result, key=lambda r: r["date"] or "", reverse=True)

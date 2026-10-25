"""Small adapters for the iOS 9 Photos, AddressBook, SMS and Notes stores."""
import re
import hashlib
import sqlite3
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import PurePosixPath

from .backup import BackupError

PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".tiff", ".bmp"}
VIDEO_TYPES = {".mov", ".mp4", ".m4v", ".3gp"}
AUDIO_TYPES = {".m4a", ".aac", ".wav", ".caf", ".amr", ".mp3"}
APPLE_EPOCH = 978307200


def apple_date(value):
    if not isinstance(value, (int, float)) or not value:
        return None
    if abs(value) > 1e12:
        value /= 1e9
    try:
        return datetime.fromtimestamp(value + APPLE_EPOCH, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


class NoteText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"br", "div", "p", "li", "h1", "h2"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"div", "p", "li", "h1", "h2"}:
            self.parts.append("\n")

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def note_text(value):
    if not isinstance(value, str):
        return "Dieser Notizinhalt ist in einem nicht unterstützten Format gespeichert."
    parser = NoteText()
    parser.feed(value)
    return re.sub(r"\n[ \t]*\n+", "\n", "".join(parser.parts)).strip()


def columns(db, table):
    return {row[1].lower() for row in db.execute(f'PRAGMA table_info("{table}")')}


def require_columns(db, table, names):
    if not set(names) <= columns(db, table):
        raise BackupError("Diese Datenbank-Version wird noch nicht unterstützt.", "unsupported_schema")


def select_all(db, table):
    rows = db.execute(f'SELECT rowid AS "_id", * FROM "{table}" LIMIT 50001').fetchall()
    if len(rows) > 50000:
        raise BackupError("Diese Sammlung ist für diese Version zu groß (mehr als 50.000 Einträge).", "too_large")
    return [{k.lower(): row[k] for k in row.keys()} for row in rows]


def identity(value):
    value = str(value or "").strip().lower()
    return re.sub(r"[^0-9+]", "", value) if "@" not in value else value


def deleted_row(row):
    return any(row.get(key) not in (None, 0, "0", "", False) for key in
               ("zmarkedfordeletion", "markedfordeletion", "deleted", "is_deleted", "isdeleted", "zdeleted", "zdatedeleted"))


def message_visibility(db, include_deleted):
    fields = columns(db, "message") & {"is_deleted", "deleted", "isdeleted"}
    return "" if include_deleted else "".join(f' AND COALESCE(m."{field}",0)=0' for field in sorted(fields))


class Library:
    def __init__(self, backup):
        self.backup = backup
        self.databases = {}
        self.notices = {}

    def close(self):
        for db in self.databases.values():
            db.close()
        self.databases.clear()

    def _database(self, path, domain="HomeDomain"):
        self.backup.require_unlocked()
        key = (domain, path)
        record = self.backup.by_path.get(key)
        if record is None:
            raise BackupError("Diese Inhalte sind in diesem Backup nicht enthalten.", "missing_content")
        if key in self.databases:
            return self.databases[key]
        wal = self.backup.by_path.get((domain, path + "-wal"))
        if wal and wal.size:
            raise BackupError("Diese Datenbank hat zusätzliche WAL-Daten. Diese Version kann sie noch nicht vollständig lesen.", "wal_present")
        data = self.backup.read(record.id)
        if not data.startswith(b"SQLite format 3\x00") or len(data) < 100:
            raise BackupError("Die Datenbank ist beschädigt oder hat ein unbekanntes Format.", "unsupported_schema")
        # A checkpointed WAL-mode header alone is safe to normalize in memory.
        data = data[:18] + b"\x01\x01" + data[20:]
        db = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            db.execute("PRAGMA temp_store=MEMORY")
            db.deserialize(data)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA query_only=ON")
            db.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        except sqlite3.Error:
            db.close()
            raise BackupError("Die Datenbank lässt sich nicht lesen. Prüfe, ob das Backup vollständig ist.") from None
        self.databases[key] = db
        return db

    def photos(self, include_deleted=False):
        self.backup.require_unlocked()
        result = []
        for record in self.backup.files.values():
            path = PurePosixPath(record.path)
            if record.domain != "CameraRollDomain" or not record.path.startswith("Media/DCIM/"):
                continue
            extension = path.suffix.lower()
            if extension not in PHOTO_TYPES | VIDEO_TYPES:
                continue
            deleted = bool(int.from_bytes(record.properties.get("com.apple.assetsd.trashed", b"\0"), "little"))
            if deleted and not include_deleted:
                continue
            result.append({"id": record.id, "name": path.name, "size": record.size,
                           "deleted": deleted, "key": record.properties.get("com.apple.assetsd.UUID", b"").hex() or record.id,
                           "kind": "video" if extension in VIDEO_TYPES else "photo",
                           "date": datetime.fromtimestamp(record.modified, timezone.utc).isoformat()})
        return sorted(result, key=lambda p: (p["date"], p["name"]), reverse=True)

    def contacts(self, include_deleted=False):
        db = self._database("Library/AddressBook/AddressBook.sqlitedb")
        require_columns(db, "ABPerson", {"first", "last"})
        values = select_all(db, "ABMultiValue") if columns(db, "ABMultiValue") else []
        by_person = {}
        for item in values:
            by_person.setdefault(item.get("record_id"), []).append(item)
        result = []
        for person in select_all(db, "ABPerson"):
            if deleted_row(person) and not include_deleted:
                continue
            name = " ".join(str(person.get(k) or "").strip() for k in ["first", "middle", "last"]).strip()
            name = re.sub(r"\s+", " ", name) or person.get("organization") or person.get("nickname") or "Ohne Namen"
            fields = by_person.get(person["_id"], [])
            result.append({"id": person["_id"], "name": name,
                           "deleted": deleted_row(person), "key": str(person.get("guid") or person.get("externalidentifier") or person["_id"]),
                           "organization": person.get("organization") or "",
                           "phones": [v["value"] for v in fields if v.get("property") == 3 and v.get("value")],
                           "emails": [v["value"] for v in fields if v.get("property") == 4 and v.get("value")]})
        return sorted(result, key=lambda c: c["name"].casefold())

    def notes(self, include_deleted=False):
        from .notes import read_notes
        return read_notes(self, include_deleted)

    def legacy_notes(self, include_deleted=False):
        db = self._database("Library/Notes/notes.sqlite")
        require_columns(db, "ZNOTE", {"z_pk", "ztitle", "zbody"})
        require_columns(db, "ZNOTEBODY", {"z_pk", "zcontent"})
        bodies = {row["z_pk"]: row["zcontent"] for row in select_all(db, "ZNOTEBODY")}
        result = [{"id": n["z_pk"], "title": n.get("ztitle") or "Ohne Titel",
                   "key": "legacy:" + str(n["z_pk"]), "deleted": deleted_row(n),
                   "body": note_text(bodies.get(n["zbody"], "")),
                   "date": apple_date(n.get("zmodificationdate") or n.get("zcreationdate"))}
                  for n in select_all(db, "ZNOTE") if include_deleted or not deleted_row(n)]
        return sorted(result, key=lambda n: n["date"] or "", reverse=True)

    def notes_notice(self):
        self.backup.require_unlocked()
        return self.notices.get("notes", "")

    def recordings(self, include_deleted=False):
        from .recordings import read_recordings
        return read_recordings(self, include_deleted)

    def _sms(self):
        db = self._database("Library/SMS/sms.db")
        require_columns(db, "message", {"text", "date", "is_from_me"})
        require_columns(db, "chat", {"chat_identifier"})
        require_columns(db, "chat_message_join", {"chat_id", "message_id"})
        return db

    def conversations(self, include_deleted=False):
        db = self._sms()
        condition = message_visibility(db, include_deleted)
        names = {}
        try:
            for contact in self.contacts():
                for value in contact["phones"] + contact["emails"]:
                    names[identity(value)] = contact["name"]
        except BackupError:
            pass  # Messages remain useful when the contact store is absent.
        result = []
        for chat in select_all(db, "chat"):
            if deleted_row(chat) and not include_deleted:
                continue
            last = db.execute(f"""SELECT m.text, m.date FROM message m
                JOIN chat_message_join j ON j.message_id=m.rowid WHERE j.chat_id=?
                {condition} ORDER BY m.date DESC, m.rowid DESC LIMIT 1""", (chat["_id"],)).fetchone()
            if not last:
                continue
            identifier = chat.get("chat_identifier") or "Unbekannt"
            result.append({"id": chat["_id"], "name": chat.get("display_name") or names.get(identity(identifier)) or identifier,
                           "key": identifier, "deleted": deleted_row(chat),
                           "preview": last["text"] or "Anhang oder Nachricht ohne Text",
                           "date": apple_date(last["date"])})
        return sorted(result, key=lambda c: c["date"] or "", reverse=True)

    def messages(self, chat_id, offset=0, limit=200, include_deleted=False):
        db = self._sms()
        limit = max(1, min(50000, limit))
        if offset < 0:
            raise BackupError("Der Nachrichtenbereich ist ungültig.")
        condition = message_visibility(db, include_deleted)
        rows = db.execute(f"""SELECT m.rowid AS _id, m.* FROM message m
            JOIN chat_message_join j ON j.message_id=m.rowid WHERE j.chat_id=?
            {condition} ORDER BY m.date DESC, m.rowid DESC LIMIT ? OFFSET ?""", (chat_id, limit + 1, offset)).fetchall()
        result = []
        for row in reversed(rows[:limit]):
            value = dict(row)
            key = value.get("guid") or hashlib.sha256(repr((row['text'], row['date'], row['is_from_me'], value.get('handle_id'))).encode()).hexdigest()
            result.append({"id": row["_id"], "text": row["text"] or "Anhang oder Nachricht ohne lesbaren Text",
                           "key": key, "deleted": deleted_row(value),
                           "from_me": bool(row["is_from_me"]), "date": apple_date(row["date"])})
        return {"items": result, "has_more": len(rows) > limit, "offset": offset + min(len(rows), limit)}

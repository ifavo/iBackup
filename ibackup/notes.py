"""Text-only Apple NoteStore reader. No HTML or embedded content is executed."""
import zlib

from .backup import BackupError
from .content import apple_date, deleted_row, require_columns, select_all

MAX_NOTE = 16 * 1024 * 1024


def protobuf_fields(data):
    position = 0

    def number():
        nonlocal position
        value = 0
        for shift in range(0, 70, 7):
            if position >= len(data):
                raise ValueError("Unvollständiges Notizformat")
            byte = data[position]
            position += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise ValueError("Ungültiges Notizformat")

    fields = {}
    while position < len(data):
        tag = number()
        field, wire = tag >> 3, tag & 7
        if not field:
            raise ValueError("Ungültiges Notizformat")
        if wire == 0:
            fields[field] = number()
            continue
        if wire not in (1, 2, 5):
            raise ValueError("Unbekanntes Notizformat")
        length = number() if wire == 2 else 8 if wire == 1 else 4
        if position + length > len(data):
            raise ValueError("Unvollständiges Notizformat")
        fields[field] = data[position:position + length]
        position += length
    return fields


def decode_note(blob):
    if not isinstance(blob, bytes) or not blob or len(blob) > MAX_NOTE:
        raise ValueError("Kein lesbarer Notiztext")
    if blob.startswith(b"\x1f\x8b") or blob[:1] == b"x":
        decoder = zlib.decompressobj(47)
        try:
            blob = decoder.decompress(blob, MAX_NOTE + 1)
        except zlib.error:
            raise ValueError("Beschädigter Notiztext") from None
        if len(blob) > MAX_NOTE or decoder.unconsumed_tail or not decoder.eof:
            raise ValueError("Zu großer oder unvollständiger Notiztext")
    try:
        # NoteStoreProto.document (2) → Document.note (3) → Note.note_text (2).
        text = protobuf_fields(protobuf_fields(protobuf_fields(blob)[2])[3])[2]
        return text.decode("utf-8").replace("\ufffc", "[Anhang]")
    except (KeyError, TypeError, AttributeError, UnicodeError):
        raise ValueError("Dieses Notizformat ist nicht lesbar") from None


def date_field(row):
    for prefix in ("zmodificationdate", "zcreationdate"):
        keys = sorted((k for k in row if k.startswith(prefix)), reverse=True)
        for key in keys:
            if row[key] is not None:
                return apple_date(row[key])
    return None


def modern_notes(library, record, include_deleted):
    db = library._database(record.path, record.domain)
    require_columns(db, "ZICNOTEDATA", {"z_pk", "zdata"})
    require_columns(db, "ZICCLOUDSYNCINGOBJECT", {"z_pk"})
    objects = {row["z_pk"]: row for row in select_all(db, "ZICCLOUDSYNCINGOBJECT")}
    by_data = {row["znotedata"]: row for row in objects.values() if row.get("znotedata") is not None}
    result = []
    for data in select_all(db, "ZICNOTEDATA"):
        row = objects.get(data.get("znote")) or by_data.get(data["z_pk"], {})
        folder = objects.get(row.get("zfolder"), {})
        deleted = deleted_row(row) or deleted_row(folder)
        fragment = not row
        if (deleted or fragment) and not include_deleted:
            continue
        protected = bool(row.get("zispasswordprotected"))
        notice = ""
        if protected:
            text = "Diese Notiz ist zusätzlich mit einem eigenen Notizpasswort geschützt."
        else:
            try:
                text = decode_note(data.get("zdata"))
            except ValueError:
                text = row.get("zsnippet") or ""
                notice = "Nur die gespeicherte Vorschau ist lesbar. Der vollständige Notiztext konnte nicht dekodiert werden."
        if "[Anhang]" in text:
            notice = (notice + " Anhänge und Tabellen werden hier nur als Platzhalter gezeigt.").strip()
        result.append({"id": f"{record.id}:{data['z_pk']}", "key": row.get("zidentifier") or f"{record.domain}:{data.get('znote', data['z_pk'])}",
                       "title": row.get("ztitle1") or row.get("ztitle") or "Notizfragment",
                       "body": text, "date": date_field(row), "deleted": deleted, "fragment": fragment,
                       "protected": protected, "notice": notice, "folder": folder.get("ztitle2") or ""})
    return result


def read_notes(library, include_deleted=False):
    library.backup.require_unlocked()
    records = [r for r in library.backup.files.values() if r.path.endswith("NoteStore.sqlite")]
    result, errors = [], []
    legacy = ("HomeDomain", "Library/Notes/notes.sqlite") in library.backup.by_path
    if legacy:
        try:
            result.extend(library.legacy_notes(include_deleted))
        except BackupError as error:
            errors.append(error)
    for record in records:
        try:
            result.extend(modern_notes(library, record, include_deleted))
        except BackupError as error:
            errors.append(error)
    if errors and not result:
        raise errors[0]
    if not legacy and not records:
        raise BackupError("Diese Inhalte sind in diesem Backup nicht enthalten.", "missing_content")
    library.notices["notes"] = "Ein Teil der klassischen oder neueren Notizspeicher ist nicht lesbar: " + " ".join(str(e) for e in errors) if errors else ""
    return sorted(result, key=lambda n: n["date"] or "", reverse=True)

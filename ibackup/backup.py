"""MBDB index and bounded, read-only access to original backup blobs."""
import hashlib
import os
import plistlib
import stat
import struct
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from pathlib import Path
from xml.parsers.expat import ExpatError

from .crypto import Keybag, decrypt

MAX_READ = 128 * 1024 * 1024
DEFAULT_ROOT = Path.home() / "Library/Application Support/MobileSync/Backup"


class BackupError(ValueError):
    def __init__(self, message, code="backup_error"):
        super().__init__(message)
        self.code = code


def open_readonly(path: Path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise BackupError("Eine Datei im Backup fehlt. Du brauchst eine vollständige Kopie.", "missing_file") from None
    except OSError:
        raise BackupError("Die Datei ist nicht lesbar oder eine Verknüpfung. Wähle den Original-Backupordner.") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise BackupError("Die Backup-Datei ist keine reguläre Datei.")
    return os.fdopen(fd, "rb")


def read_small(path: Path, maximum=MAX_READ):
    with open_readonly(path) as stream:
        if os.fstat(stream.fileno()).st_size > maximum:
            raise BackupError("Diese Datei ist für die Speicheransicht zu groß.", "too_large")
        return stream.read(maximum + 1)


def read_plist(path):
    if not path.is_file():
        raise BackupError(f"In diesem Ordner fehlt {path.name}. Du brauchst eine vollständige Backup-Kopie.", "missing_file")
    data = read_small(path, 32 * 1024 * 1024)
    try:
        result = plistlib.loads(data)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (plistlib.InvalidFileException, ValueError, TypeError, ExpatError, OverflowError):
        raise BackupError("Die Backup-Metadaten sind beschädigt. Wähle ein vollständiges Backup.") from None


@dataclass(frozen=True)
class Record:
    id: str
    domain: str
    path: str
    size: int
    modified: int
    mode: int
    protection_class: int
    encryption_key: bytes
    properties: dict = dataclass_field(default_factory=dict)


def parse_mbdb(data: bytes) -> list[Record]:
    if not data.startswith(b"mbdb\x05\x00"):
        raise BackupError("Der Backup-Index ist beschädigt oder kein MBDB-Backup.")
    position = 6

    def take(size):
        nonlocal position
        end = position + size
        if end > len(data):
            raise BackupError("Der Backup-Index ist beschädigt: Ein Eintrag ist unvollständig.")
        value = data[position:end]
        position = end
        return value

    def field():
        size = int.from_bytes(take(2), "big")
        return b"" if size == 65535 else take(size)

    records = []
    while position < len(data):
        domain_bytes, path_bytes = field(), field()
        field()  # Symlink target: never followed.
        field()  # Digest; original length determines CBC padding removal.
        key = field()
        mode, _, _, _, modified, _, _, size, protection, count = struct.unpack(">HQIIIIIQBB", take(40))
        properties = {}
        for _ in range(count):
            name, value = field(), field()
            properties[name.decode("utf-8", errors="replace")] = value
        try:
            domain, path = domain_bytes.decode("utf-8"), path_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise BackupError("Der Backup-Index enthält einen beschädigten Dateinamen.") from None
        fid = hashlib.sha1(domain_bytes + b"-" + path_bytes).hexdigest()
        records.append(Record(fid, domain, path, size, modified, mode, protection, key, properties))
    return records


def iso_date(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()


def inspect_layout(path, records=None):
    """Unindexed blobs are evidence of extra files, never a dated snapshot."""
    if records is None:
        if not (path / "Manifest.mbdb").is_file():
            return None
        records = parse_mbdb(read_small(path / "Manifest.mbdb"))
    indexed = {record.id for record in records if stat.S_ISREG(record.mode)}
    with os.scandir(path) as entries:
        present = {entry.name for entry in entries if entry.is_file(follow_symlinks=False)}
    blobs = {name for name in present if len(name) == 40 and all(c in "0123456789abcdef" for c in name)}
    extra, missing = len(blobs - indexed), len(indexed - present)
    notices = []
    if extra:
        notices.append(f"{extra} zusätzliche Dateien ohne Zuordnung zum Backup-Index. "
                       "Möglicherweise wurden Backupordner zusammengeführt. Daraus entsteht kein zweiter Zeitstand. "
                       "Füge die zweite vollständige Kopie als getrennten Ordner hinzu.")
    if missing:
        notices.append(f"{missing} Dateien aus dem Backup-Index fehlen. Du brauchst eine vollständige Kopie.")
    return {"unindexed_files": extra, "missing_files": missing, "notice": " ".join(notices)}


def describe(path: Path, info: dict, manifest: dict, records=None) -> dict:
    for field in ["Product Type", "Product Version", "Device Name", "Display Name"]:
        if field in info and not isinstance(info[field], str):
            raise BackupError("Die Geräteangaben im Backup sind beschädigt.")
    if "IsEncrypted" in manifest and not isinstance(manifest["IsEncrypted"], bool):
        raise BackupError("Die Verschlüsselungsangabe im Backup ist beschädigt.")
    product = info.get("Product Type", "iOS-Gerät")
    device = info.get("Unique Identifier") or info.get("Target Identifier") or manifest.get("Lockdown", {}).get("UniqueDeviceID")
    model = "iPod touch" if product.startswith("iPod") else "iPad" if product.startswith("iPad") else "iPhone" if product.startswith("iPhone") else product
    return {
        "id": hashlib.sha256(str(path).encode()).hexdigest()[:20],
        "name": info.get("Device Name") or info.get("Display Name") or model,
        "model": model, "product": product, "ios": info.get("Product Version", "–"),
        "date": iso_date(info.get("Last Backup Date") or manifest.get("Date")),
        "encrypted": bool(manifest.get("IsEncrypted")), "path": str(path),
        "supported": (path / "Manifest.mbdb").is_file(),
        "device_id": hashlib.sha256(str(device).encode()).hexdigest() if device else None,
        "layout": inspect_layout(path, records),
    }


def discover(roots=None):
    items = []
    for root in roots if roots is not None else [DEFAULT_ROOT]:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        candidates = [root] if (root / "Info.plist").is_file() else sorted(root.iterdir())
        for path in candidates:
            if not path.is_dir() or not (path / "Info.plist").is_file():
                continue
            try:
                path = path.resolve()
                items.append(describe(path, read_plist(path / "Info.plist"), read_plist(path / "Manifest.plist")))
            except (BackupError, OSError):
                continue
    return items


class Backup:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.info = read_plist(self.path / "Info.plist")
        self.manifest = read_plist(self.path / "Manifest.plist")
        describe(self.path, self.info, self.manifest)
        if not (self.path / "Manifest.mbdb").is_file():
            raise BackupError("Dieses Backup nutzt Manifest.db. Version 1 liest ältere MBDB-Backups (iOS 4–9).", "unsupported")
        self.records = parse_mbdb(read_small(self.path / "Manifest.mbdb"))
        self.files = {r.id: r for r in self.records if stat.S_ISREG(r.mode)}
        self.by_path = {(r.domain, r.path): r for r in self.files.values()}
        self.encrypted = bool(self.manifest.get("IsEncrypted"))
        self.unlocked = False
        if self.encrypted and not isinstance(self.manifest.get("BackupKeyBag"), bytes):
            raise BackupError("Der Backup-Schlüsselbund fehlt oder ist beschädigt.")
        self.keybag = Keybag(self.manifest.get("BackupKeyBag", b"")) if self.encrypted else None

    def metadata(self):
        return {**describe(self.path, self.info, self.manifest, self.records), "file_count": len(self.files), "unlocked": self.unlocked}

    def unlock(self, password):
        self.lock()
        if self.keybag:
            self.keybag.unlock(password)
        self.unlocked = True

    def lock(self):
        self.unlocked = False
        if self.keybag:
            self.keybag.keys.clear()

    def require_unlocked(self):
        if not self.unlocked:
            raise BackupError("Du musst das Backup zuerst entsperren.", "locked")

    def record(self, file_id):
        record = self.files.get(file_id)
        if not record:
            raise BackupError("Diese Datei gehört nicht zum geöffneten Backup.", "not_found")
        return record

    def read(self, file_id, start=0, end=None):
        self.require_unlocked()
        record = self.record(file_id)
        end = record.size if end is None else end
        if not 0 <= start <= end <= record.size:
            raise BackupError("Der angeforderte Dateibereich ist ungültig.")
        if end - start > MAX_READ:
            raise BackupError("Diese Datei ist für die Speicheransicht zu groß.", "too_large")
        if start == end:
            return b""
        path = self.path / record.id
        if not self.encrypted:
            with open_readonly(path) as stream:
                if os.fstat(stream.fileno()).st_size < record.size:
                    raise BackupError("Die Backup-Datei ist beschädigt oder unvollständig.")
                stream.seek(start)
                data = stream.read(end - start)
            if len(data) != end - start:
                raise BackupError("Die Backup-Datei ist beschädigt oder unvollständig.")
            return data
        if not record.encryption_key:
            raise BackupError("Für diese Datei fehlt der Verschlüsselungsschlüssel.")
        key = self.keybag.file_key(record.protection_class, record.encryption_key[4:])
        first = start // 16 * 16
        last = (end + 15) // 16 * 16
        with open_readonly(path) as stream:
            if os.fstat(stream.fileno()).st_size < (record.size + 15) // 16 * 16:
                raise BackupError("Die verschlüsselte Datei ist beschädigt oder unvollständig.")
            if first:
                stream.seek(first - 16)
                iv = stream.read(16)
            else:
                iv = bytes(16)
            data = stream.read(last - first)
        if len(data) != last - first or len(iv) != 16:
            raise BackupError("Die verschlüsselte Datei ist beschädigt oder unvollständig.")
        return decrypt(data, key, iv)[start - first:end - first]

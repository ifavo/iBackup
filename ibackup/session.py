"""Unlocked snapshot collection and honest, provenance-preserving history views."""
import threading
from datetime import datetime

from .backup import Backup, BackupError, discover
from .content import Library


def timestamp(metadata):
    try:
        return datetime.fromisoformat(metadata["date"]).timestamp()
    except (ValueError, TypeError, KeyError):
        return None


class Store:
    def __init__(self, roots):
        self.roots = roots
        self.known = {}
        self.backup = None
        self.library = None
        self.snapshots = {}
        self.generation = 0
        self.mutex = threading.RLock()

    def discover(self):
        for item in discover(self.roots):
            self.known[item["id"]] = item
        return sorted(({**item, "unlocked": key in self.snapshots} for key, item in self.known.items()),
                      key=lambda item: timestamp(item) or 0, reverse=True)

    def lock(self):
        for library in self.snapshots.values():
            library.close()
            library.backup.lock()
        self.snapshots.clear()
        if self.backup:
            self.backup.lock()
        self.library = None
        self.generation += 1

    def require_library(self, source=None):
        library = self.snapshots.get(source) if source else self.library
        if library is None:
            raise BackupError("Du musst das Backup zuerst entsperren.", "locked")
        return library

    def open(self, backup_id, password):
        item = self.known.get(backup_id)
        if not item:
            raise BackupError("Wähle zuerst ein lokales Backup.")
        try:
            backup = Backup(item["path"])
            backup.unlock(password)
        except Exception:
            self.lock()
            raise
        previous = self.snapshots.pop(backup_id, None)
        if previous:
            previous.close()
            previous.backup.lock()
        self.snapshots[backup_id] = Library(backup)
        return self.select(backup_id)

    def select(self, backup_id):
        item = self.known.get(backup_id)
        if not item:
            raise BackupError("Wähle zuerst einen vorhandenen Backup-Stand.")
        self.library = self.snapshots.get(backup_id)
        self.backup = self.library.backup if self.library else Backup(item["path"])
        self.generation += 1
        return self.status()

    def status(self):
        return {"backup": self.backup.metadata() if self.backup else None,
                "generation": self.generation, "unlocked_count": len(self.snapshots)}

    @staticmethod
    def is_older(candidate, current):
        left, right = timestamp(candidate), timestamp(current)
        return bool(current.get("device_id") and current["device_id"] == candidate.get("device_id")
                    and candidate["id"] != current["id"] and left is not None and right is not None and left <= right)

    def older(self, library):
        metadata = library.backup.metadata()
        return sorted((other for other in self.snapshots.values() if self.is_older(other.backup.metadata(), metadata)),
                      key=lambda other: timestamp(other.backup.metadata()), reverse=True)

    def decorate(self, items, library):
        metadata = library.backup.metadata()
        active_id = self.backup.metadata()["id"]
        return [{**item, "source_id": metadata["id"], "source_date": metadata["date"],
                 "historical": metadata["id"] != active_id} for item in items]

    def bundle(self, section, include_history=False):
        library = self.require_library()
        notices = []
        try:
            items = self.decorate(getattr(library, section)(include_deleted=include_history), library)
            notices.append(library.notices.get(section, ""))
        except BackupError as error:
            if not include_history:
                raise
            items = []
            notices.append("Im aktuellen Stand ist dieser Bereich nicht lesbar: " + str(error))
        if include_history:
            seen = {str(item["key"]) for item in items}
            for previous in self.older(library):
                try:
                    old_items = self.decorate(getattr(previous, section)(include_deleted=True), previous)
                    for item in old_items:
                        if str(item["key"]) not in seen:
                            items.append(item)
                            seen.add(str(item["key"]))
                    if previous.notices.get(section):
                        notices.append("Ein älterer Stand ist nur teilweise lesbar: " + previous.notices[section])
                except (BackupError, ValueError):
                    notices.append("Dieser Bereich ist in einem älteren Stand nicht lesbar.")
            locked = sum(self.is_older(item, self.backup.metadata()) and key not in self.snapshots for key, item in self.known.items())
            if locked:
                notices.append(f"{locked} ältere Stände sind noch gesperrt. Öffne sie einmal, um sie einzubeziehen.")
            notices.append("Gelöscht = erhaltene Löschmarkierung. Historisch = nur in einem älteren geöffneten Stand gefunden; kein Nachweis einer Löschung.")
        return {"items": items, "notice": " ".join(dict.fromkeys(n for n in notices if n))}

    def messages(self, chat_id, source=None, include_history=False, offset=0):
        if offset < 0:
            raise BackupError("Der Nachrichtenbereich ist ungültig.")
        library = self.require_library(source)
        if not include_history:
            page = library.messages(chat_id, offset=offset)
            page["items"] = self.decorate(page["items"], library)
            return page
        chat = next((chat for chat in library.conversations(True) if chat["id"] == chat_id), None)
        if chat is None:
            raise BackupError("Das Gespräch ist in diesem Stand nicht vorhanden.", "not_found")
        items, seen, notices = [], set(), []
        for candidate in [library, *self.older(library)]:
            try:
                match = next((item for item in candidate.conversations(True) if item["key"] == chat["key"]), None)
                if match is None:
                    continue
                page = candidate.messages(match["id"], limit=50000, include_deleted=True)
                if page["has_more"]:
                    raise BackupError("Dieser Verlauf umfasst mehr als 50.000 Nachrichten pro Stand.", "too_large")
                for item in self.decorate(page["items"], candidate):
                    if item["key"] not in seen:
                        items.append(item)
                        seen.add(item["key"])
            except BackupError as error:
                if candidate is library:
                    raise
                notices.append("Ein älterer Gesprächsverlauf ist nicht vollständig lesbar.")
        items.sort(key=lambda item: (item["date"] or "", str(item["key"])))
        end = max(0, len(items) - offset)
        start = max(0, end - 200)
        return {"items": items[start:end], "offset": offset + end - start, "has_more": start > 0, "notice": " ".join(notices)}

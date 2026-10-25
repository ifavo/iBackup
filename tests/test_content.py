import pytest

from tests.fixtures import PASSWORD, database, library_files, make_backup


def library(tmp_path, files=None):
    from ibackup.backup import Backup
    from ibackup.content import Library
    make_backup(tmp_path, files or library_files())
    backup = Backup(tmp_path)
    backup.unlock(PASSWORD)
    return Library(backup)


def test_photos_are_originals_and_keep_video_types(tmp_path):
    photos = library(tmp_path).photos()
    assert len(photos) == 2
    assert {p["kind"] for p in photos} == {"photo", "video"}
    assert all("Thumbnails" not in p["name"] for p in photos)


def test_contacts_preserve_phone_email_and_organization(tmp_path):
    contacts = library(tmp_path).contacts()
    ada = next(c for c in contacts if c["name"] == "Ada Lovelace")
    assert ada["phones"] == ["+49 123"]
    assert ada["emails"] == ["ada@example.invalid"]
    assert any(c["name"] == "Studio" for c in contacts)


def test_notes_are_plain_text_not_executable_html(tmp_path):
    notes = library(tmp_path).notes()
    assert notes[0]["title"] == "Eine Erinnerung"
    assert notes[0]["body"] == "Ein schöner Tag.\nAm See & im Wald."
    assert "script" not in notes[0]["body"]


def test_conversations_resolve_contacts_and_page_in_order(tmp_path):
    lib = library(tmp_path)
    chats = lib.conversations()
    assert chats[0]["name"] == "Ada Lovelace"
    assert chats[0]["preview"] == "Bis später."
    page = lib.messages(1, limit=1)
    assert page["items"][0]["text"] == "Bis später."
    assert page["items"][0]["from_me"] is True
    assert page["has_more"] is True
    previous = lib.messages(1, offset=1, limit=1)
    assert previous["items"][0]["text"] == "Hallo!"
    assert previous["has_more"] is False


def test_missing_empty_and_unsupported_are_distinct(tmp_path):
    from ibackup.backup import BackupError
    lib = library(tmp_path, {("HomeDomain", "Library/Notes/notes.sqlite"): database("CREATE TABLE unknown (x TEXT)")})
    with pytest.raises(BackupError) as missing:
        lib.contacts()
    assert missing.value.code == "missing_content"
    with pytest.raises(BackupError) as unsupported:
        lib.notes()
    assert unsupported.value.code == "unsupported_schema"
    empty = library(tmp_path / "empty", {("HomeDomain", "Library/Notes/notes.sqlite"): database("""
        CREATE TABLE ZNOTE (Z_PK INTEGER, ZTITLE TEXT, ZBODY INTEGER);
        CREATE TABLE ZNOTEBODY (Z_PK INTEGER, ZCONTENT TEXT);
    """)})
    assert empty.notes() == []


def test_nonempty_wal_is_reported_instead_of_silently_losing_data(tmp_path):
    from ibackup.backup import BackupError
    files = library_files()
    files[("HomeDomain", "Library/Notes/notes.sqlite-wal")] = b"wal-content"
    with pytest.raises(BackupError) as error:
        library(tmp_path, files).notes()
    assert error.value.code == "wal_present"


def test_lock_blocks_even_cached_content(tmp_path):
    lib = library(tmp_path)
    assert lib.contacts()
    lib.backup.lock()
    with pytest.raises(ValueError, match="entsperren"):
        lib.contacts()


def test_sqlite_temporary_data_stays_in_memory(tmp_path):
    lib = library(tmp_path)
    lib.contacts()
    assert all(db.execute("PRAGMA temp_store").fetchone()[0] == 2 for db in lib.databases.values())


def test_notestore_only_is_unsupported_and_mixed_store_is_disclosed(tmp_path):
    from ibackup.backup import BackupError
    modern = {("AppDomainGroup-group.com.apple.notes", "NoteStore.sqlite"): database("CREATE TABLE ZICCLOUDSYNCINGOBJECT (Z_PK INTEGER)")}
    lib = library(tmp_path / "modern", modern)
    with pytest.raises(BackupError) as error:
        lib.notes()
    assert error.value.code == "unsupported_schema"
    mixed = library(tmp_path / "mixed", {**library_files(), **modern})
    assert len(mixed.notes()) == 1
    assert "neueren" in mixed.notes_notice()

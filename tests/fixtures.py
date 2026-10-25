"""Independent writer for tiny synthetic iTunes backups. No personal data."""
import hashlib
import plistlib
import sqlite3
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.keywrap import aes_key_wrap

PASSWORD = "test-only-password"
CLASS_KEY = bytes(range(32))
FILE_KEY = bytes(reversed(range(32)))


def field(value: bytes) -> bytes:
    return struct.pack(">H", len(value)) + value


def tlv(tag, value):
    if isinstance(value, int):
        value = struct.pack(">I", value)
    return tag.encode() + struct.pack(">I", len(value)) + value


def database(sql: str) -> bytes:
    with sqlite3.connect(":memory:") as db:
        db.executescript(sql)
        return db.serialize()


def make_backup(root: Path, files=None, encrypted=True, double_kdf=False, properties=None):
    root.mkdir(parents=True, exist_ok=True)
    files = files or {("HomeDomain", "Documents/hello.txt"): b"hello backup\x01"}
    salt = b"test salt for backup!"
    password = PASSWORD.encode()
    extra = b""
    if double_kdf:
        extra = tlv("DPSL", b"second salt") + tlv("DPIC", 2)
        password = hashlib.pbkdf2_hmac("sha256", password, b"second salt", 2, 32)
    derived = hashlib.pbkdf2_hmac("sha1", password, salt, 100, 32)
    keybag = (tlv("VERS", 3) + tlv("TYPE", 1) + tlv("UUID", b"b" * 16)
              + tlv("WRAP", 2) + tlv("SALT", salt) + tlv("ITER", 100) + extra
              + tlv("UUID", b"c" * 16) + tlv("CLAS", 3) + tlv("WRAP", 2)
              + tlv("WPKY", aes_key_wrap(derived, CLASS_KEY)))
    (root / "Info.plist").write_bytes(plistlib.dumps({
        "Device Name": "Test iPod", "Product Type": "iPod5,1", "Product Version": "9.3.5",
        "Unique Identifier": "synthetic-device-1",
    }))
    (root / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": encrypted, "BackupKeyBag": keybag}))
    manifest = bytearray(b"mbdb\x05\x00")
    ids = {}
    for (domain, path), data in files.items():
        fid = hashlib.sha1(f"{domain}-{path}".encode()).hexdigest()
        ids[path] = fid
        key = struct.pack("<I", 3) + aes_key_wrap(CLASS_KEY, FILE_KEY) if encrypted else b""
        for value in [domain.encode(), path.encode(), b"", hashlib.sha1(data).digest(), key]:
            manifest.extend(field(value))
        attrs = (properties or {}).get(path, {})
        manifest.extend(struct.pack(">HQIIIIIQBB", 0o100644, 1, 501, 501, 1700000000, 1700000000, 1700000000, len(data), 3, len(attrs)))
        for name, value in attrs.items():
            manifest.extend(field(name.encode()) + field(value))
        if encrypted:
            padding = 16 - len(data) % 16
            enc = Cipher(algorithms.AES(FILE_KEY), modes.CBC(bytes(16))).encryptor()
            data = enc.update(data + bytes([padding]) * padding) + enc.finalize()
        (root / fid).write_bytes(data)
    (root / "Manifest.mbdb").write_bytes(manifest)
    return ids


def note_blob(text):
    import gzip
    def number(value):
        out = bytearray()
        while value > 127:
            out.append((value & 127) | 128)
            value >>= 7
        out.append(value)
        return bytes(out)
    def message(field, data):
        return number(field * 8 + 2) + number(len(data)) + data
    return gzip.compress(message(2, b'\x08\x00\x10\x00' + message(3, message(2, text.encode()))))


def modern_notes_files():
    sql = """
        CREATE TABLE ZICCLOUDSYNCINGOBJECT (Z_PK INTEGER, ZIDENTIFIER TEXT, ZTITLE1 TEXT,
          ZSNIPPET TEXT, ZMODIFICATIONDATE1 REAL, ZMARKEDFORDELETION INTEGER, ZISPASSWORDPROTECTED INTEGER);
        CREATE TABLE ZICNOTEDATA (Z_PK INTEGER, ZNOTE INTEGER, ZDATA BLOB);
    """
    for pk, title, body, deleted, locked in [
        (1, 'Aktuelle Notiz', note_blob('Hallo\nVollständiger Text 🌿'), 0, 0),
        (2, 'Alte Notiz', note_blob('Noch vorhanden'), 1, 0),
        (3, 'Geschützt', b'ciphertext', 0, 1),
        (4, 'Defekt', b'not-a-protobuf', 0, 0),
    ]:
        sql += f"INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES ({pk},'note-{pk}','{title}','Vorschau',700000000,{deleted},{locked});"
        sql += f"INSERT INTO ZICNOTEDATA VALUES ({pk},{pk},X'{body.hex()}');"
    return {('AppDomainGroup-group.com.apple.notes', 'NoteStore.sqlite'): database(sql)}


def library_files():
    return {
        ("HomeDomain", "Library/AddressBook/AddressBook.sqlitedb"): database("""
            CREATE TABLE ABPerson (First TEXT, Last TEXT, Organization TEXT);
            CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT, label TEXT);
            INSERT INTO ABPerson VALUES ('Ada', 'Lovelace', NULL), (NULL, NULL, 'Studio');
            INSERT INTO ABMultiValue VALUES (1, 3, '+49 123', '_$!<Mobile>!$_'), (1, 4, 'ada@example.invalid', NULL);
        """),
        ("HomeDomain", "Library/Notes/notes.sqlite"): database("""
            CREATE TABLE ZNOTE (Z_PK INTEGER, ZTITLE TEXT, ZBODY INTEGER, ZCREATIONDATE REAL, ZMODIFICATIONDATE REAL);
            CREATE TABLE ZNOTEBODY (Z_PK INTEGER, ZCONTENT TEXT);
            INSERT INTO ZNOTE VALUES (1, 'Eine Erinnerung', 7, 700000000, 700000100);
            INSERT INTO ZNOTEBODY VALUES (7, '<div>Ein schöner Tag.</div><script>steal()</script><div>Am See &amp; im Wald.</div>');
        """),
        ("HomeDomain", "Library/SMS/sms.db"): database("""
            CREATE TABLE handle (id TEXT);
            CREATE TABLE chat (display_name TEXT, chat_identifier TEXT);
            CREATE TABLE message (text TEXT, date REAL, is_from_me INTEGER, handle_id INTEGER);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
            INSERT INTO handle VALUES ('+49 123');
            INSERT INTO chat VALUES (NULL, '+49 123');
            INSERT INTO chat_handle_join VALUES (1, 1);
            INSERT INTO message VALUES ('Hallo!', 700000000, 0, 1), ('Bis später.', 700000010, 1, 0);
            INSERT INTO chat_message_join VALUES (1, 1), (1, 2);
        """),
        ("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0001.JPG"): b"image-fixture",
        ("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0002.MOV"): b"video-fixture",
        ("CameraRollDomain", "Media/PhotoData/Thumbnails/IMG_0001.JPG"): b"thumbnail",
    }

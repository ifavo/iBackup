"""Apple backup keybags; crypto primitives come from OpenSSL/cryptography."""
import hashlib
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap


def decrypt(data: bytes, key: bytes, iv: bytes = bytes(16)) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return cipher.update(data) + cipher.finalize()


class Keybag:
    def __init__(self, data: bytes):
        self.attributes = {}
        self.classes = []
        self.keys = {}
        current = self.attributes
        position = 0
        seen_uuid = False
        while position < len(data):
            if position + 8 > len(data):
                raise ValueError("Der Schlüsselbund ist beschädigt.")
            tag = data[position:position + 4].decode("ascii")
            size = struct.unpack_from(">I", data, position + 4)[0]
            position += 8
            value = data[position:position + size]
            if len(value) != size:
                raise ValueError("Der Schlüsselbund ist beschädigt.")
            position += size
            if tag == "UUID":
                if seen_uuid:
                    current = {}
                    self.classes.append(current)
                seen_uuid = True
            if tag in {"VERS", "TYPE", "WRAP", "ITER", "DPIC", "CLAS", "KTYP"}:
                if size != 4:
                    raise ValueError("Der Schlüsselbund ist beschädigt.")
                value = int.from_bytes(value, "big")
            current[tag] = value

    def unlock(self, password: str):
        self.keys.clear()
        attrs = self.attributes
        try:
            secret = password.encode("utf-8")
            if "DPSL" in attrs:
                secret = hashlib.pbkdf2_hmac("sha256", secret, attrs["DPSL"], self._iterations("DPIC"), 32)
            secret = hashlib.pbkdf2_hmac("sha1", secret, attrs["SALT"], self._iterations("ITER"), 32)
            keys = {}
            for item in self.classes:
                if not item.get("WRAP", 0) & 2 or "WPKY" not in item:
                    continue
                keys[item["CLAS"] & 15] = aes_key_unwrap(secret, item["WPKY"])
            if not keys:
                raise ValueError("Keine unterstützten Schlüssel im Backup.")
            self.keys = keys
        except InvalidUnwrap:
            raise ValueError("Das Passwort passt nicht. Du brauchst das Passwort dieses Backups.") from None
        except KeyError:
            raise ValueError("Der Schlüsselbund ist unvollständig. Du brauchst ein vollständiges Backup.") from None

    def _iterations(self, name):
        value = self.attributes[name]
        if not isinstance(value, int) or not 1 <= value <= 20_000_000:
            raise ValueError("Dieser Schlüsselbund nutzt nicht unterstützte Passwortparameter.")
        return value

    def file_key(self, protection_class: int, wrapped: bytes) -> bytes:
        key = self.keys.get(protection_class & 15)
        if key is None:
            raise ValueError("Für diese Datei ist kein lesbarer Schlüssel vorhanden.")
        try:
            return aes_key_unwrap(key, wrapped)
        except InvalidUnwrap:
            raise ValueError("Der Dateischlüssel ist beschädigt.") from None

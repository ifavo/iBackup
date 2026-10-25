import hashlib
from pathlib import Path

import pytest

from tests.fixtures import PASSWORD, make_backup


def reader(path):
    from ibackup.backup import Backup
    return Backup(path)


def test_reads_encrypted_backup_without_changing_any_source_bytes(tmp_path):
    ids = make_backup(tmp_path)
    before = {p.name: hashlib.sha256(p.read_bytes()).digest() for p in tmp_path.iterdir()}
    backup = reader(tmp_path)
    assert backup.metadata()["encrypted"] is True
    assert backup.metadata()["file_count"] == 1
    backup.unlock(PASSWORD)
    assert backup.read(ids["Documents/hello.txt"]) == b"hello backup\x01"
    backup.lock()
    assert {p.name: hashlib.sha256(p.read_bytes()).digest() for p in tmp_path.iterdir()} == before


def test_wrong_password_and_lock_deny_content(tmp_path):
    ids = make_backup(tmp_path)
    backup = reader(tmp_path)
    with pytest.raises(ValueError, match="Passwort"):
        backup.unlock("wrong")
    with pytest.raises(ValueError, match="entsperren"):
        backup.read(next(iter(ids.values())))
    backup.unlock(PASSWORD)
    backup.lock()
    with pytest.raises(ValueError, match="entsperren"):
        backup.read(next(iter(ids.values())))


@pytest.mark.parametrize("start,end", [(0, 1), (7, 40), (16, 32), (250, 256), (0, 256)])
def test_unaligned_encrypted_byte_ranges(tmp_path, start, end):
    data = bytes(range(256))
    ids = make_backup(tmp_path, {("CameraRollDomain", "Media/DCIM/movie.mov"): data})
    backup = reader(tmp_path)
    backup.unlock(PASSWORD)
    assert backup.read(next(iter(ids.values())), start, end) == data[start:end]


def test_unencrypted_and_double_kdf(tmp_path):
    for encrypted, double in [(False, False), (True, True)]:
        path = tmp_path / str(encrypted)
        ids = make_backup(path, encrypted=encrypted, double_kdf=double)
        backup = reader(path)
        backup.unlock(PASSWORD if encrypted else "")
        assert backup.read(next(iter(ids.values()))) == b"hello backup\x01"


def test_truncated_manifest_is_rejected(tmp_path):
    make_backup(tmp_path)
    manifest = tmp_path / "Manifest.mbdb"
    manifest.write_bytes(manifest.read_bytes()[:-5])
    with pytest.raises(ValueError, match="beschädigt"):
        reader(tmp_path)


def test_missing_file_and_symlink_never_reads_outside_backup(tmp_path):
    root = tmp_path / "backup"
    ids = make_backup(root)
    fid = next(iter(ids.values()))
    blob = root / fid
    blob.unlink()
    backup = reader(root)
    backup.unlock(PASSWORD)
    with pytest.raises(ValueError, match="fehlt"):
        backup.read(fid)
    outside = tmp_path / "private"
    outside.write_bytes(b"not a backup file")
    blob.symlink_to(outside)
    with pytest.raises(ValueError, match="Verknüpfung"):
        backup.read(fid)


def test_modern_manifest_has_clear_error(tmp_path):
    make_backup(tmp_path)
    (tmp_path / "Manifest.mbdb").unlink()
    (tmp_path / "Manifest.db").write_bytes(b"unsupported")
    with pytest.raises(ValueError, match="Manifest.db"):
        reader(tmp_path)


def test_discovery_skips_truncated_xml_and_wrong_metadata_types(tmp_path):
    from ibackup.backup import discover
    make_backup(tmp_path / "valid")
    make_backup(tmp_path / "broken")
    (tmp_path / "broken" / "Info.plist").write_bytes(b'<?xml version="1.0"?><plist><dict>')
    make_backup(tmp_path / "wrong-types")
    import plistlib
    (tmp_path / "wrong-types" / "Info.plist").write_bytes(plistlib.dumps({"Product Type": 42}))
    assert len(discover([tmp_path])) == 1


def test_partial_read_detects_truncated_blob_before_streaming(tmp_path):
    ids = make_backup(tmp_path, {("CameraRollDomain", "Media/DCIM/large.mov"): b"x" * 1000})
    fid = next(iter(ids.values()))
    blob = tmp_path / fid
    blob.write_bytes(blob.read_bytes()[:32])
    backup = reader(tmp_path)
    backup.unlock(PASSWORD)
    with pytest.raises(ValueError, match="unvollständig"):
        backup.read(fid, 0, 1)


def test_merged_files_are_reported_without_inventing_another_snapshot(tmp_path):
    from ibackup.backup import discover
    root = tmp_path / "merged"
    make_backup(root)
    extra = root / ("a" * 40)
    extra.write_bytes(b"another backup's unindexed file")
    (root / ".DS_Store").write_bytes(b"ignored")
    items = discover([tmp_path])
    assert len(items) == 1
    assert items[0]["layout"]["unindexed_files"] == 1
    assert items[0]["layout"]["missing_files"] == 0
    assert items[0]["layout"]["notice"]
    assert reader(root).metadata()["layout"] == items[0]["layout"]
    assert extra.read_bytes() == b"another backup's unindexed file"


def test_layout_reports_missing_blobs_without_following_symlinks(tmp_path):
    ids = make_backup(tmp_path / "backup")
    root = tmp_path / "backup"
    blob = root / next(iter(ids.values()))
    blob.unlink()
    blob.symlink_to(tmp_path / "outside")
    assert reader(root).metadata()["layout"]["missing_files"] == 1

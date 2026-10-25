import plistlib
from datetime import datetime

import pytest

from tests.fixtures import PASSWORD, database, make_backup, modern_notes_files


def lib(tmp_path, files):
    from ibackup.backup import Backup
    from ibackup.content import Library
    make_backup(tmp_path, files)
    backup = Backup(tmp_path)
    backup.unlock(PASSWORD)
    return Library(backup)


def test_modern_notes_decode_text_and_preserve_deleted_protected_and_corrupt_states(tmp_path):
    library = lib(tmp_path, modern_notes_files())
    notes = library.notes()
    assert len(notes) == 3
    assert next(n for n in notes if n['title'] == 'Aktuelle Notiz')['body'] == 'Hallo\nVollständiger Text 🌿'
    assert next(n for n in notes if n['title'] == 'Geschützt')['protected'] is True
    broken = next(n for n in notes if n['title'] == 'Defekt')
    assert broken['notice'] and broken['body'] == 'Vorschau'
    all_notes = library.notes(include_deleted=True)
    assert len(all_notes) == 4
    assert next(n for n in all_notes if n['title'] == 'Alte Notiz')['deleted'] is True


def test_voice_recordings_keep_titles_duration_and_unindexed_files(tmp_path):
    files = {
        ('MediaDomain', 'Media/Recordings/first.m4a'): b'audio one',
        ('MediaDomain', 'Media/Recordings/second.m4a'): b'audio two',
        ('MediaDomain', 'Media/Recordings/orphan.m4a'): b'audio three',
        ('MediaDomain', 'Media/Recordings/Recordings.db'): database('''
            CREATE TABLE recordings (path TEXT, title TEXT, date REAL, duration REAL, deleted INTEGER);
            INSERT INTO recordings VALUES ('first.m4a','Am Meer',700000000,61.5,0),('second.m4a','Gelöscht',700000010,20,1);
        '''),
    }
    library = lib(tmp_path, files)
    current = library.recordings()
    assert len(current) == 2
    assert next(r for r in current if r['name'] == 'Am Meer')['duration'] == 61.5
    assert next(r for r in current if r['name'] == 'orphan.m4a')['unindexed'] is True
    assert len(library.recordings(include_deleted=True)) == 3


def test_note_protobuf_rejects_truncation_and_decompression_bombs():
    from ibackup.notes import decode_note
    import gzip
    with pytest.raises(ValueError):
        decode_note(gzip.compress(b'\x12\xff\xff'))
    with pytest.raises(ValueError):
        decode_note(gzip.compress(b'x' * (16 * 1024 * 1024 + 1)))


def test_deleted_photo_properties_are_opt_in(tmp_path):
    from ibackup.backup import Backup
    from ibackup.content import Library
    files = {('CameraRollDomain','Media/DCIM/one.jpg'): b'one', ('CameraRollDomain','Media/DCIM/two.jpg'): b'two'}
    make_backup(tmp_path,files,properties={'Media/DCIM/two.jpg': {'com.apple.assetsd.trashed': b'\x01\x00'}})
    backup=Backup(tmp_path);backup.unlock(PASSWORD);library=Library(backup)
    assert len(library.photos()) == 1
    assert len(library.photos(True)) == 2


def test_audio_stream_is_authenticated_and_byte_accurate(tmp_path):
    from fastapi.testclient import TestClient
    from ibackup.server import create_app
    from tests.test_server import ORIGIN, HEADERS, unlock
    make_backup(tmp_path, {('MediaDomain','Media/Recordings/test.wav'): b'0123456789'})
    with TestClient(create_app([tmp_path],'test-token',8765),base_url=ORIGIN) as client:
        status=unlock(client)
        item=client.get('/api/recordings').json()['items'][0]
        response=client.get(f"/api/media/{item['id']}?source={item['source_id']}&generation={status['generation']}",headers={'Range':'bytes=2-5'})
        assert response.status_code == 206 and response.content == b'2345'
        assert response.headers['content-type'].startswith('audio/')


def test_deleted_message_is_not_used_as_current_chat_preview(tmp_path):
    from tests.fixtures import library_files
    import sqlite3
    files=library_files()
    key=('HomeDomain','Library/SMS/sms.db')
    with sqlite3.connect(':memory:') as db:
        db.deserialize(files[key]);db.execute('ALTER TABLE message ADD COLUMN is_deleted INTEGER DEFAULT 0')
        db.execute('UPDATE message SET is_deleted=1 WHERE rowid=2');db.commit();files[key]=db.serialize()
    library=lib(tmp_path,files)
    assert library.conversations()[0]['preview'] == 'Hallo!'
    assert library.conversations(True)[0]['preview'] == 'Bis später.'
    assert len(library.messages(1)['items']) == 1


def test_unrelated_app_recording_database_cannot_relabel_or_hide_apple_memo(tmp_path):
    files={('MediaDomain','Media/Recordings/original.m4a'):b'audio',
           ('AppDomain-other.recorder','Documents/Recordings.db'):database("CREATE TABLE recordings(path TEXT,title TEXT,deleted INTEGER); INSERT INTO recordings VALUES('original.m4a','Wrong title',1);")}
    items=lib(tmp_path,files).recordings()
    assert len(items)==1
    assert items[0]['name']=='original.m4a' and not items[0]['deleted']


@pytest.mark.parametrize('prefix', ['/var/mobile/', '/private/var/mobile/', 'file:///var/mobile/'])
def test_ios9_absolute_recording_paths_keep_metadata(tmp_path, prefix):
    files = {
        ('MediaDomain', 'Media/Recordings/memo.m4a'): b'audio',
        ('MediaDomain', 'Media/Recordings/Recordings.db'): database(f'''
            CREATE TABLE ZRECORDING (ZPATH TEXT, ZCUSTOMLABEL TEXT, ZDATE REAL, ZDURATION REAL);
            INSERT INTO ZRECORDING VALUES ('{prefix}Media/Recordings/memo.m4a','Testaufnahme',700000000,12.5);
        '''),
    }
    item = lib(tmp_path, files).recordings()[0]
    assert item['name'] == 'Testaufnahme'
    assert item['duration'] == 12.5
    assert item['date'] is not None and item['unindexed'] is False


def test_absolute_path_outside_voice_memos_cannot_match_by_basename(tmp_path):
    files = {
        ('MediaDomain', 'Media/Recordings/memo.m4a'): b'audio',
        ('MediaDomain', 'Media/Recordings/Recordings.db'): database('''
            CREATE TABLE ZRECORDING (ZPATH TEXT, ZCUSTOMLABEL TEXT);
            INSERT INTO ZRECORDING VALUES ('/var/mobile/Other/memo.m4a','Wrong');
        '''),
    }
    item = lib(tmp_path, files).recordings()[0]
    assert item['name'] == 'memo.m4a' and item['unindexed'] is True

import plistlib
from datetime import datetime

from tests.fixtures import PASSWORD, database, make_backup


def snapshot(root, date, texts, device='same-device'):
    sql = 'CREATE TABLE ZNOTE (Z_PK INTEGER,ZTITLE TEXT,ZBODY INTEGER); CREATE TABLE ZNOTEBODY (Z_PK INTEGER,ZCONTENT TEXT);'
    for pk, text in texts.items():
        sql += f"INSERT INTO ZNOTE VALUES ({pk},'{text}',{pk}); INSERT INTO ZNOTEBODY VALUES ({pk},'{text}');"
    make_backup(root, {('HomeDomain', 'Library/Notes/notes.sqlite'): database(sql)})
    path = root / 'Info.plist'
    info = plistlib.loads(path.read_bytes())
    info.update({'Last Backup Date': datetime.fromisoformat(date), 'Unique Identifier': device})
    path.write_bytes(plistlib.dumps(info))


def test_history_merges_only_older_same_device_and_keeps_provenance(tmp_path):
    from ibackup.session import Store
    snapshot(tmp_path/'old', '2025-01-01', {1:'Damals',2:'Früher'})
    snapshot(tmp_path/'now', '2026-01-01', {1:'Heute'})
    snapshot(tmp_path/'other', '2024-01-01', {9:'Fremdes Gerät'}, 'other-device')
    store = Store([tmp_path])
    backups = store.discover()
    by_name = {item['path'].split('/')[-1]: item for item in backups}
    for name in ['old','other','now']:
        store.open(by_name[name]['id'], PASSWORD)
    current = store.bundle('notes', False)['items']
    assert [n['title'] for n in current] == ['Heute']
    merged = store.bundle('notes', True)['items']
    assert {n['title'] for n in merged} == {'Heute','Früher'}
    old = next(n for n in merged if n['title']=='Früher')
    assert old['historical'] is True and old['deleted'] is False
    assert old['source_id'] == by_name['old']['id']
    store.select(by_name['old']['id'])
    assert {n['title'] for n in store.bundle('notes',True)['items']} == {'Damals','Früher'}
    assert not any(n['historical'] for n in store.bundle('notes',True)['items'])
    store.lock()
    assert not store.snapshots
    assert all(not item['unlocked'] for item in store.discover())


def test_locked_history_is_disclosed(tmp_path):
    from ibackup.session import Store
    snapshot(tmp_path/'old', '2025-01-01', {2:'Früher'})
    snapshot(tmp_path/'now', '2026-01-01', {1:'Heute'})
    store=Store([tmp_path]);items=store.discover()
    store.open(next(i['id'] for i in items if i['path'].endswith('/now')),PASSWORD)
    assert 'gesperrt' in store.bundle('notes',True)['notice']


def test_history_still_shows_older_notes_when_current_store_is_missing(tmp_path):
    from ibackup.session import Store
    snapshot(tmp_path/'old', '2025-01-01', {2:'Früher'})
    snapshot(tmp_path/'now', '2026-01-01', {1:'Heute'})
    # A valid current backup can omit an entire app store.
    path=tmp_path/'now'; info=(path/'Info.plist').read_bytes()
    make_backup(path,{('HomeDomain','Documents/readme.txt'):b'current'},encrypted=True)
    (path/'Info.plist').write_bytes(info)
    store=Store([tmp_path]);items=store.discover()
    for suffix in ['/old','/now']:
        store.open(next(i['id'] for i in items if i['path'].endswith(suffix)),PASSWORD)
    result=store.bundle('notes',True)
    assert [item['title'] for item in result['items']] == ['Früher']
    assert result['items'][0]['historical'] is True
    assert 'aktuellen' in result['notice']


def test_refresh_discovers_separate_copy_without_relocking_current_backup(tmp_path):
    from ibackup.session import Store
    snapshot(tmp_path/'now', '2026-01-01', {1:'Heute'})
    store = Store([tmp_path])
    current = store.discover()[0]
    store.open(current['id'], PASSWORD)
    snapshot(tmp_path/'old', '2025-01-01', {2:'Früher'})
    refreshed = store.discover()
    assert len(refreshed) == 2
    assert store.status()['backup']['unlocked'] is True
    assert store.bundle('notes')['items'][0]['title'] == 'Heute'

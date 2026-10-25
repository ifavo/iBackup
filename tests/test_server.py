from fastapi.testclient import TestClient
import pytest

from tests.fixtures import PASSWORD, library_files, make_backup

ORIGIN = "http://127.0.0.1:8765"
HEADERS = {"Origin": ORIGIN, "X-iBackup": "1"}


@pytest.fixture
def client(tmp_path):
    from ibackup.server import create_app
    make_backup(tmp_path, library_files())
    with TestClient(create_app([tmp_path], "test-token", 8765), base_url=ORIGIN) as client:
        yield client


def login(client):
    response = client.post("/api/session", json={"token": "test-token"}, headers=HEADERS)
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "ibackup_session_8765=" in response.headers["set-cookie"]


def unlock(client):
    login(client)
    backup = client.get("/api/backups").json()[0]
    response = client.post("/api/open", json={"id": backup["id"], "password": PASSWORD}, headers=HEADERS)
    assert response.status_code == 200
    return response.json()


def test_no_content_without_local_session(client):
    assert client.get("/api/backups").status_code == 401
    assert client.post("/api/session", json={"token": "wrong"}, headers=HEADERS).status_code == 401
    login(client)
    assert client.get("/api/photos").status_code == 423


def test_host_origin_and_csrf_protection(client):
    login(client)
    assert client.get("/api/backups", headers={"Host": "evil.invalid"}).status_code == 403
    assert client.get("/api/backups", headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert client.post("/api/lock", headers={"Origin": ORIGIN}).status_code == 403
    assert client.post("/api/lock", headers={"Origin": "null", "X-iBackup": "1"}).status_code == 403


def test_reads_four_apps_and_lock_revokes_media(client):
    state = unlock(client)
    for app in ["photos", "contacts", "notes", "conversations"]:
        response = client.get("/api/" + app)
        assert response.status_code == 200
        assert response.json()
        assert response.headers["cache-control"] == "no-store"
    photo = client.get("/api/photos").json()["items"][0]
    url = f'/api/media/{photo["id"]}?generation={state["generation"]}'
    response = client.get(url, headers={"Range": "bytes=1-4"})
    assert response.status_code == 206
    assert response.content == b"ideo"  # newest name is IMG_0002.MOV
    assert response.headers["content-range"] == "bytes 1-4/13"
    assert client.get(url, headers={"Range": "bytes=200-300"}).status_code == 416
    assert client.post("/api/lock", headers=HEADERS).status_code == 200
    assert client.get(url).status_code == 423
    assert client.get("/api/contacts").status_code == 423


def test_wrong_password_leaves_previous_session_locked(client):
    unlock(client)
    backup = client.get("/api/backups").json()[0]
    result = client.post("/api/open", json={"id": backup["id"], "password": "wrong"}, headers=HEADERS)
    assert result.status_code == 422
    assert client.get("/api/contacts").status_code == 423


def test_path_picker_and_stale_media_generation(client, tmp_path):
    state = unlock(client)
    photo = client.get("/api/photos").json()["items"][0]
    assert client.get(f'/api/media/{photo["id"]}?generation={state["generation"] - 1}').status_code == 409
    response = client.post("/api/inspect", json={"path": str(tmp_path)}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["name"] == "Test iPod"


def test_suffix_range_and_invalid_ranges(client):
    state = unlock(client)
    photo = client.get("/api/photos").json()["items"][0]
    url = f'/api/media/{photo["id"]}?generation={state["generation"]}'
    assert client.get(url, headers={"Range": "bytes=-7"}).content == b"fixture"
    assert client.get(url, headers={"Range": "bytes=no"}).status_code == 416
    assert client.get(url, headers={"Range": "bytes=0-1,3-4"}).status_code == 416


def test_empty_import_explains_missing_metadata_and_keeps_current_session(client, tmp_path):
    unlock(client)
    empty = tmp_path / 'empty-copy'
    empty.mkdir()
    response = client.post('/api/inspect', json={'path':str(empty)}, headers=HEADERS)
    assert response.status_code == 422
    assert 'Info.plist' in response.json()['error']
    assert client.get('/api/status').json()['backup']['unlocked'] is True

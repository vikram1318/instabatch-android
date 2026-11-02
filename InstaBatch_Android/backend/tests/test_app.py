import os
import time
import uuid

import pytest

os.environ.setdefault("SECRET_KEY", "test-import-secret")
os.environ.setdefault("APP_PASSWORD", "test-import-password")

from app import create_app


CLIENT_ID = str(uuid.uuid4())
OTHER_CLIENT_ID = str(uuid.uuid4())


def fake_downloader(_url, output_dir, _quality, _config):
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "sample-video.mp4"
    path.write_bytes(b"video")
    return path


@pytest.fixture
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "APP_PASSWORD": "test-password",
            "SESSION_COOKIE_SECURE": False,
            "DATABASE_PATH": str(tmp_path / "state.sqlite3"),
            "DATA_DIR": str(tmp_path / "data"),
            "DOWNLOADER": fake_downloader,
        }
    )
    yield application
    application.extensions["job_executor"].shutdown(wait=True)


def login(client, client_id=CLIENT_ID, password="test-password"):
    return client.post(
        "/login",
        data={"password": password},
        headers={"X-Client-ID": client_id},
    )


def wait_for_job(client, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/status/{job_id}")
        assert response.status_code == 200
        result = response.get_json()
        if result["status"] in {"done", "error"}:
            return result
        time.sleep(0.01)
    pytest.fail("Background job did not finish in time")


def test_health_endpoint(app):
    response = app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_login_requires_correct_password_and_client_id(app):
    client = app.test_client()
    assert login(client, password="wrong").status_code == 401
    assert client.post("/login", data={"password": "test-password"}).status_code == 400
    assert login(client).status_code == 200


def test_download_job_and_files_are_scoped_to_client(app):
    client = app.test_client()
    assert client.post("/api/download", json={}).status_code == 401
    assert login(client).status_code == 200

    response = client.post(
        "/api/download",
        json={"urls": ["https://www.instagram.com/reel/ABC123/"], "quality": "720p"},
    )
    assert response.status_code == 202
    job = wait_for_job(client, response.get_json()["job_id"])
    assert job["status"] == "done"
    assert job["done"] == 1

    files = client.get("/api/files").get_json()
    assert len(files) == 1
    file_response = client.get(files[0]["url"])
    assert file_response.status_code == 200
    assert file_response.data == b"video"

    other = app.test_client()
    assert login(other, client_id=OTHER_CLIENT_ID).status_code == 200
    assert other.get("/api/files").get_json() == []
    assert other.get(files[0]["url"]).status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "http://www.instagram.com/reel/ABC123/",
        "https://example.com/reel/ABC123/",
        "https://instagram.com.evil.example/reel/ABC123/",
        "https://user:pass@www.instagram.com/reel/ABC123/",
        "https://www.instagram.com/explore/",
    ],
)
def test_rejects_non_instagram_or_unsafe_urls(app, url):
    client = app.test_client()
    assert login(client).status_code == 200
    response = client.post(
        "/api/download",
        json={"urls": [url], "quality": "best"},
    )
    assert response.status_code == 400


def test_limits_batch_size_and_quality(app):
    client = app.test_client()
    assert login(client).status_code == 200
    too_many = ["https://www.instagram.com/reel/ABC123/"] * 11
    assert client.post(
        "/api/download", json={"urls": too_many, "quality": "best"}
    ).status_code == 400
    assert client.post(
        "/api/download",
        json={"urls": ["https://www.instagram.com/reel/ABC123/"], "quality": "4k"},
    ).status_code == 400
from __future__ import annotations

import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, request, send_file, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

from storage import (
    cleanup_expired,
    connect,
    create_job,
    get_file,
    get_job,
    initialize_database,
    list_files,
    recover_interrupted_jobs,
)
from worker import download_instagram_video, run_job


ALLOWED_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am", "www.instagr.am"}
ALLOWED_QUALITIES = {"best", "1080p", "720p", "480p"}
CLIENT_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
INSTAGRAM_PATH_RE = re.compile(r"/(?:p|reel|reels|tv)/[A-Za-z0-9_-]+/?", re.IGNORECASE)
cleanup_lock = threading.Lock()
last_cleanup_at = 0.0


def _integer_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _client_id() -> str | None:
    value = request.headers.get("X-Client-ID", "")
    if not CLIENT_ID_RE.fullmatch(value):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _rate_limit_key() -> str:
    return session.get("client_id") or get_remote_address()


def _valid_instagram_url(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        return (
            parsed.scheme == "https"
            and hostname in ALLOWED_HOSTS
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and bool(INSTAGRAM_PATH_RE.fullmatch(parsed.path))
        )
    except ValueError:
        return False


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    default_data_dir = Path(os.environ.get("DATA_DIR", "/tmp/instabatch-data"))
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        APP_PASSWORD=os.environ.get("APP_PASSWORD"),
        DATABASE_PATH=os.environ.get("DATABASE_PATH", "/tmp/instabatch.sqlite3"),
        DATA_DIR=str(default_data_dir),
        MAX_WORKERS=_integer_env("MAX_WORKERS", 3, 1, 3),
        MAX_ACTIVE_JOBS=_integer_env("MAX_ACTIVE_JOBS", 8, 1, 30),
        MAX_CLIENT_JOBS=_integer_env("MAX_CLIENT_JOBS", 2, 1, 5),
        RATE_LIMIT=_integer_env("RATE_LIMIT", 6, 1, 30),
        MAX_FILE_MB=_integer_env("MAX_FILE_MB", 250, 1, 1024),
        CLEANUP_HOURS=_integer_env("CLEANUP_HOURS", 24, 1, 168),
        RATELIMIT_STORAGE_URI=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "true").lower() != "false",
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        if app.testing:
            app.config["SECRET_KEY"] = "test-only-secret"
        else:
            raise RuntimeError("Set SECRET_KEY to a long, random value before starting the service.")
    if not app.config.get("APP_PASSWORD") and not app.testing:
        raise RuntimeError("Set APP_PASSWORD before starting the service; unauthenticated access is disabled.")

    data_dir = Path(app.config["DATA_DIR"]).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    Path(app.config["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    app.config["DATA_DIR"] = str(data_dir)
    initialize_database(app.config["DATABASE_PATH"])
    recover_interrupted_jobs(app.config["DATABASE_PATH"])

    limiter = Limiter(
        key_func=_rate_limit_key,
        app=app,
        storage_uri=app.config["RATELIMIT_STORAGE_URI"],
        default_limits=[],
    )
    app.extensions["limiter"] = limiter
    app.extensions["job_executor"] = ThreadPoolExecutor(
        max_workers=app.config["MAX_WORKERS"], thread_name_prefix="instabatch"
    )
    app.extensions["downloader"] = app.config.get("DOWNLOADER", download_instagram_video)

    @app.before_request
    def remove_expired_files() -> None:
        global last_cleanup_at
        now = time.monotonic()
        if now - last_cleanup_at < 300:
            return
        with cleanup_lock:
            if now - last_cleanup_at >= 300:
                cleanup_expired(
                    app.config["DATABASE_PATH"],
                    app.config["CLEANUP_HOURS"],
                )
                last_cleanup_at = now

    @app.errorhandler(429)
    def too_many_requests(_error):
        return jsonify(error="Too many requests. Please wait and try again."), 429

    @app.get("/healthz")
    @limiter.limit("30 per minute")
    def health():
        return jsonify(status="ok")

    @app.post("/login")
    @limiter.limit("10 per minute", key_func=get_remote_address)
    def login():
        client_id = _client_id()
        if client_id is None:
            return jsonify(error="A valid X-Client-ID header is required."), 400
        supplied = request.form.get("password", "")
        if request.is_json:
            supplied = (request.get_json(silent=True) or {}).get("password", "")
        expected = app.config.get("APP_PASSWORD") or ""
        if not expected or not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
            return jsonify(error="Invalid app password."), 401
        session.clear()
        session.permanent = True
        session["client_id"] = client_id
        return jsonify(status="ok")

    @app.post("/api/download")
    @limiter.limit(f"{app.config['RATE_LIMIT']} per hour")
    def submit_download():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify(error="Log in before submitting downloads."), 401
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="A JSON request body is required."), 400
        urls = payload.get("urls")
        quality = payload.get("quality", "best")
        if not isinstance(urls, list) or not 1 <= len(urls) <= 10:
            return jsonify(error="Submit between 1 and 10 Instagram URLs."), 400
        if any(not _valid_instagram_url(url) for url in urls):
            return jsonify(error="Only HTTPS Instagram post or reel URLs are accepted."), 400
        if quality not in ALLOWED_QUALITIES:
            return jsonify(error="Unsupported video quality."), 400

        database_path = app.config["DATABASE_PATH"]
        with connect(database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            global_active = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
            client_active = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE client_id = ? AND status IN ('queued', 'running')",
                (client_id,),
            ).fetchone()[0]
            if global_active >= app.config["MAX_ACTIVE_JOBS"]:
                db.rollback()
                return jsonify(error="The service is busy. Please try again later."), 503
            if client_active >= app.config["MAX_CLIENT_JOBS"]:
                db.rollback()
                return jsonify(error="You already have the maximum number of active batches."), 429
            job_id = secrets.token_urlsafe(18)
            create_job(db, job_id, client_id, len(urls))
        app.extensions["job_executor"].submit(
            run_job,
            app,
            job_id,
            client_id,
            urls,
            quality,
        )
        return jsonify(job_id=job_id), 202

    @app.get("/api/status/<job_id>")
    @limiter.limit("60 per minute")
    def job_status(job_id: str):
        client_id = session.get("client_id")
        if not client_id:
            return jsonify(error="Log in to check job status."), 401
        job = get_job(app.config["DATABASE_PATH"], job_id, client_id)
        if job is None:
            abort(404)
        return jsonify(job)

    @app.get("/api/files")
    @limiter.limit("30 per minute")
    def available_files():
        client_id = session.get("client_id")
        if not client_id:
            return jsonify(error="Log in to list files."), 401
        files = list_files(app.config["DATABASE_PATH"], client_id)
        return jsonify(
            [
                {
                    "id": item["id"],
                    "filename": item["filename"],
                    "url": f"/api/files/{item['id']}/download",
                }
                for item in files
            ]
        )

    @app.get("/api/files/<file_id>/download")
    @limiter.limit("30 per minute")
    def download_file(file_id: str):
        client_id = session.get("client_id")
        if not client_id:
            return jsonify(error="Log in to download files."), 401
        item = get_file(app.config["DATABASE_PATH"], file_id, client_id)
        if item is None:
            abort(404)
        file_path = Path(item["path"]).resolve()
        if not file_path.is_relative_to(Path(app.config["DATA_DIR"])) or not file_path.is_file():
            abort(404)
        return send_file(
            file_path,
            as_attachment=True,
            download_name=item["filename"],
            conditional=True,
            max_age=0,
        )

    return app


app = create_app()
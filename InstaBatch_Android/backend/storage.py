from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager


@contextmanager
def connect(database_path: str):
    db = sqlite3.connect(database_path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize_database(database_path: str) -> None:
    with connect(database_path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                status TEXT NOT NULL,
                total INTEGER NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                logs TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_client_created
                ON jobs(client_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS jobs_active
                ON jobs(status, created_at);
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                path TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS files_client_created
                ON files(client_id, created_at DESC);
            """
        )


def recover_interrupted_jobs(database_path: str) -> None:
    now = time.time()
    with connect(database_path) as db:
        db.execute(
            """UPDATE jobs SET status = 'error',
               logs = ?, updated_at = ?
               WHERE status IN ('queued', 'running')""",
            (json.dumps(["The service restarted before this batch finished."]), now),
        )


def create_job(db: sqlite3.Connection, job_id: str, client_id: str, total: int) -> None:
    now = time.time()
    db.execute(
        "INSERT INTO jobs(id, client_id, status, total, done, logs, created_at, updated_at) "
        "VALUES (?, ?, 'queued', ?, 0, ?, ?, ?)",
        (job_id, client_id, total, json.dumps(["Batch queued."]), now, now),
    )


def update_job(
    database_path: str,
    job_id: str,
    *,
    status: str | None = None,
    done: int | None = None,
    log: str | None = None,
) -> None:
    with connect(database_path) as db:
        row = db.execute("SELECT logs FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return
        assignments = ["updated_at = ?"]
        values: list[object] = [time.time()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if done is not None:
            assignments.append("done = ?")
            values.append(done)
        if log is not None:
            logs = json.loads(row["logs"])
            logs.append(log[:240])
            assignments.append("logs = ?")
            values.append(json.dumps(logs[-60:]))
        values.append(job_id)
        db.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", values)


def get_job(database_path: str, job_id: str, client_id: str) -> dict | None:
    with connect(database_path) as db:
        row = db.execute(
            "SELECT id, status, total, done, logs FROM jobs WHERE id = ? AND client_id = ?",
            (job_id, client_id),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["logs"] = json.loads(result["logs"])
    return result


def add_file(
    database_path: str,
    file_id: str,
    client_id: str,
    job_id: str,
    filename: str,
    path: str,
) -> None:
    with connect(database_path) as db:
        db.execute(
            "INSERT INTO files(id, client_id, job_id, filename, path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (file_id, client_id, job_id, filename, path, time.time()),
        )


def list_files(database_path: str, client_id: str) -> list[dict]:
    with connect(database_path) as db:
        rows = db.execute(
            "SELECT id, filename FROM files WHERE client_id = ? ORDER BY created_at DESC",
            (client_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_file(database_path: str, file_id: str, client_id: str) -> dict | None:
    with connect(database_path) as db:
        row = db.execute(
            "SELECT id, filename, path FROM files WHERE id = ? AND client_id = ?",
            (file_id, client_id),
        ).fetchone()
    return dict(row) if row else None


def cleanup_expired(database_path: str, keep_hours: int) -> None:
    cutoff = time.time() - keep_hours * 3600
    with connect(database_path) as db:
        expired = db.execute(
            "SELECT id, path FROM files WHERE created_at < ?", (cutoff,)
        ).fetchall()
        for item in expired:
            try:
                os.remove(item["path"])
            except FileNotFoundError:
                pass
        db.execute("DELETE FROM files WHERE created_at < ?", (cutoff,))
        db.execute(
            "DELETE FROM jobs WHERE created_at < ? AND status NOT IN ('queued', 'running')",
            (cutoff,),
        )
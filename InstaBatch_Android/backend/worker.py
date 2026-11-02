from __future__ import annotations

import secrets
from pathlib import Path

from storage import add_file, update_job


VIDEO_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov"}


def _format_selector(quality: str) -> str:
    heights = {"1080p": 1080, "720p": 720, "480p": 480}
    if quality == "best":
        return "best[ext=mp4]/best"
    height = heights[quality]
    return f"best[height<={height}][ext=mp4]/best[height<={height}]/best"


def download_instagram_video(url: str, output_dir: Path, quality: str, config: dict) -> Path:
    import yt_dlp

    output_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = config["MAX_FILE_MB"] * 1024 * 1024

    def enforce_download_limit(progress: dict) -> None:
        if progress.get("downloaded_bytes", 0) > max_bytes:
            raise yt_dlp.utils.DownloadError("Video exceeds the configured file-size limit.")

    options = {
        "format": _format_selector(quality),
        "outtmpl": str(output_dir / "%(id)s-%(title).80B.%(ext)s"),
        "noplaylist": True,
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "socket_timeout": 20,
        "retries": 1,
        "max_filesize": max_bytes,
        "progress_hooks": [enforce_download_limit],
        "overwrites": False,
    }
    with yt_dlp.YoutubeDL(options) as client:
        info = client.extract_info(url, download=True)
        expected = Path(client.prepare_filename(info))
    candidates = [
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if expected.is_file():
        candidates.insert(0, expected)
    if not candidates:
        raise RuntimeError("No supported video file was produced.")
    result = candidates[0]
    if result.stat().st_size > max_bytes:
        result.unlink(missing_ok=True)
        raise RuntimeError("The video exceeds the configured file-size limit.")
    return result


def run_job(app, job_id: str, client_id: str, urls: list[str], quality: str) -> None:
    database_path = app.config["DATABASE_PATH"]
    root = Path(app.config["DATA_DIR"]).resolve()
    job_dir = root / "videos" / client_id / job_id
    update_job(database_path, job_id, status="running", log="Batch started.")
    completed = 0
    saved = 0
    try:
        for index, url in enumerate(urls, start=1):
            item_dir = job_dir / str(index)
            try:
                file_path = Path(
                    app.extensions["downloader"](
                        url,
                        item_dir,
                        quality,
                        app.config,
                    )
                ).resolve()
                if not file_path.is_relative_to(item_dir.resolve()):
                    raise RuntimeError("Downloader returned an invalid file path.")
                if not file_path.is_file() or file_path.suffix.lower() not in VIDEO_EXTENSIONS:
                    raise RuntimeError("No supported video file was produced.")
                filename = file_path.name[:180]
                add_file(
                    database_path,
                    secrets.token_urlsafe(18),
                    client_id,
                    job_id,
                    filename,
                    str(file_path),
                )
                saved += 1
                update_job(database_path, job_id, log=f"Item {index}: video ready.")
            except Exception as exc:
                item_dir.mkdir(parents=True, exist_ok=True)
                for path in item_dir.iterdir():
                    if path.is_file():
                        path.unlink(missing_ok=True)
                update_job(
                    database_path,
                    job_id,
                    log=f"Item {index}: download failed ({type(exc).__name__}).",
                )
            completed = index
            update_job(database_path, job_id, done=completed)
        final_status = "done" if saved else "error"
        update_job(database_path, job_id, status=final_status)
        if not saved:
            update_job(database_path, job_id, log="No videos could be downloaded.")
    except Exception as exc:
        update_job(
            database_path,
            job_id,
            status="error",
            done=completed,
            log=f"Batch stopped ({type(exc).__name__}).",
        )
    finally:
        try:
            for directory in sorted(job_dir.rglob("*"), reverse=True):
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
        except OSError:
            pass
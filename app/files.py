import re
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.config import settings

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(filename: str) -> str:
    cleaned = SAFE_NAME_RE.sub("_", Path(filename).name).strip("._")
    return cleaned or "attachment"


def challenge_attachment_dir(challenge_id: int, slug: str) -> Path:
    return settings.upload_dir / "challenges" / f"{challenge_id}_{slug}" / "attachments"


def save_upload(upload: UploadFile, challenge_id: int, slug: str) -> tuple[str, str, int]:
    directory = challenge_attachment_dir(challenge_id, slug)
    directory.mkdir(parents=True, exist_ok=True)

    original = upload.filename or "attachment"
    filename = safe_filename(original)
    stored = f"{uuid4().hex[:8]}_{filename}"
    path = directory / stored

    with path.open("wb") as output:
        shutil.copyfileobj(upload.file, output)

    return stored, str(path.as_posix()), path.stat().st_size

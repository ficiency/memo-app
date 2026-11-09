"""Files the memos use (pictures people add, slides they upload): stored once by content, read by asset.

Assets are rows (kind, content type, hash, where the bytes are). The bytes live on this machine's disk for
now, under one folder; Supabase Storage (or any object store) replaces `_write` and `path` without anything
else changing. The same picture added twice is one asset. Nothing here knows any firm.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO
from app.db.memo import Asset

ROOT = Path(os.environ.get("ASSETS_DIR") or REPO / "backend" / ".work" / "assets")
PICTURES = {"image/png", "image/jpeg", "image/gif", "image/bmp", "image/tiff", "image/x-emf", "image/x-wmf", "image/svg+xml"}
MAX_BYTES = 20 * 1024 * 1024


def put(session: Session, data: bytes, kind: str, firm_id, content_type: str | None = None, name: str | None = None) -> Asset:
    """Store a file (once per firm and content) and return its asset; the caller commits."""
    if not data:
        raise ValueError("the file is empty")
    if len(data) > MAX_BYTES:
        raise ValueError("the file is larger than 20 MB")
    content_type = content_type or (mimetypes.guess_type(name or "")[0] if name else None) or "application/octet-stream"
    digest = hashlib.sha256(data).hexdigest()
    found = session.scalar(select(Asset).where(Asset.hash == digest, Asset.firm_id == firm_id, Asset.kind == kind))
    if found is not None and Path(found.storage_path).exists():
        return found
    ext = mimetypes.guess_extension(content_type) or Path(name or "").suffix or ".bin"
    path = _write(f"{digest[:2]}/{digest}{ext}", data)
    asset = Asset(id=uuid.uuid4(), firm_id=firm_id, kind=kind, storage_path=str(path), content_type=content_type, hash=digest)
    session.add(asset)
    session.flush()
    return asset


def _write(key: str, data: bytes) -> Path:
    path = ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    return path


def path(asset: Asset) -> Path:
    return Path(asset.storage_path)


def is_picture(content_type: str | None) -> bool:
    return (content_type or "").lower() in PICTURES

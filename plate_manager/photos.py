"""Photo storage: import files into the data folder, read their date, cache thumbnails.

Originals are copied (never moved) into

    <data_dir>/photos/<well-uid>/<photo-uid><ext>

so the photo library survives the microscope PC being wiped and travels inside
an export bundle.  Thumbnails are a derived cache and can be deleted freely.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from . import paths
from .db import new_uid

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
IMAGE_FILTER = ("Images (*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp *.gif);;"
                "All files (*)")
_EXIF_DATE_TAGS = (36867, 36868, 306)  # DateTimeOriginal, DateTimeDigitized, DateTime


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def guess_taken_at(path: str | Path) -> str:
    """Best guess at when a photo was taken: EXIF first, then file dates.

    Returns an ISO date ('YYYY-MM-DD'); '' if nothing can be read.
    """
    path = Path(path)
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            for tag in _EXIF_DATE_TAGS:
                raw = exif.get(tag)
                if not raw:
                    continue
                text = str(raw).strip().replace("/", ":")
                for fmt in ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d"):
                    try:
                        return datetime.strptime(text[:len(fmt) + 2].strip(), fmt).date(
                            ).isoformat()
                    except ValueError:
                        continue
    except Exception:
        pass
    try:
        stat = path.stat()
        stamp = min(stat.st_mtime, getattr(stat, "st_ctime", stat.st_mtime))
        return datetime.fromtimestamp(stamp).date().isoformat()
    except OSError:
        return ""


def store_file(src: str | Path, well_uid: str) -> str:
    """Copy `src` into the photo folder; returns its path relative to photos/."""
    src = Path(src)
    folder = paths.photos_dir() / well_uid
    folder.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower() or ".jpg"
    name = f"{new_uid()}{suffix}"
    shutil.copy2(src, folder / name)
    return f"{well_uid}/{name}"


def absolute(rel_path: str) -> Path:
    return paths.photos_dir() / rel_path


def delete_files(rel_paths: list[str] | tuple[str, ...]) -> None:
    for rel in rel_paths:
        if not rel:
            continue
        try:
            absolute(rel).unlink(missing_ok=True)
        except OSError:
            pass
        for size in (128, 512):
            thumb_path(rel, size).unlink(missing_ok=True)


def thumb_path(rel_path: str, size: int) -> Path:
    stem = rel_path.replace("/", "_").replace("\\", "_")
    return paths.thumbs_dir() / f"{size}_{stem}.jpg"


def thumbnail(rel_path: str, size: int = 128) -> Path | None:
    """Return a cached square-ish thumbnail, generating it on first use."""
    source = absolute(rel_path)
    if not source.exists():
        return None
    thumb = thumb_path(rel_path, size)
    try:
        if thumb.exists() and thumb.stat().st_mtime >= source.stat().st_mtime:
            return thumb
        thumb.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((size, size))
            img.convert("RGB").save(thumb, "JPEG", quality=85)
        return thumb
    except Exception:
        return None

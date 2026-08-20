from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..clients.musicbrainz import TrackMetadata

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str) -> str:
    name = _INVALID_CHARS.sub("_", name).strip()
    return name or "Unknown"


def library_path_for(library_dir: str, meta: TrackMetadata, source_path: Path) -> Path:
    artist_dir = sanitize(meta.artist or "Unknown Artist")
    album_name = sanitize(meta.album or "Unknown Album")
    if meta.year:
        album_name = f"{album_name} ({meta.year[:4]})"

    track_no = f"{meta.track_number:02d} - " if meta.track_number else ""
    filename = sanitize(f"{track_no}{meta.title or source_path.stem}") + source_path.suffix.lower()

    return Path(library_dir) / artist_dir / album_name / filename


def move_into_library(source_path: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination = destination.with_stem(destination.stem + "_dup")
    shutil.move(str(source_path), str(destination))
    return destination

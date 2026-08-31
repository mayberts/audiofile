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
    if meta.album_disambiguation:
        # Two distinct albums that share both a title and a release year
        # (e.g. Weezer's "Teal Album" and "Black Album", both self-titled,
        # both 2019) would otherwise compute the exact same destination
        # folder and get physically merged together on disk -- the
        # MusicBrainz disambiguation that exists precisely to distinguish
        # them ("Teal Album" / "Black Album") keeps that from happening.
        album_name = f"{album_name} [{sanitize(meta.album_disambiguation)}]"

    track_no = f"{meta.track_number:02d} - " if meta.track_number else ""
    filename = sanitize(f"{track_no}{meta.title or source_path.stem}") + source_path.suffix.lower()

    return Path(library_dir) / artist_dir / album_name / filename


def move_into_library(source_path: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        # A single fixed "_dup" suffix can itself collide again — e.g. two
        # unrelated tracks both mistagged onto the same computed path (see
        # the disc-numbering bug this was written alongside). shutil.move
        # doesn't refuse an existing destination, it silently overwrites
        # it, so a third file landing on an already-"_dup"'d path would
        # quietly destroy a previously-moved file with no error anywhere.
        # Counting up instead guarantees every file gets its own distinct
        # destination, so a real tagging bug shows up as a pile of
        # oddly-suffixed files to investigate instead of silent data loss.
        stem, suffix = destination.stem, destination.suffix
        candidate = destination.with_name(f"{stem}_dup{suffix}")
        n = 2
        while candidate.exists():
            candidate = destination.with_name(f"{stem}_dup{n}{suffix}")
            n += 1
        destination = candidate
    shutil.move(str(source_path), str(destination))
    return destination

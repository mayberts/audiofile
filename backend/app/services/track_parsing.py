from __future__ import annotations

import re
from pathlib import PureWindowsPath

# Matches a leading "01 - ", "01. ", "01_", "1-01 - " track marker — the
# near-universal prefix on a Soulseek folder-rip filename once the artist
# and album are already established by the folder itself. The optional
# leading group handles disc-qualified numbering ("1-01", "2-05"), common
# on multi-disc "Special Edition" releases (a bonus remix disc, say) —
# without it, a filename like "1-01 - Pop.flac" has its DISC digit
# mistaken for the track number by a plain "first standalone 1-2 digit
# token" search (it finds "1" before ever reaching "01"), so every track
# on a disc ends up parsed as if it were track "1", "2", etc. — silently
# collapsing an entire disc's worth of files onto one tagged track.
_LEADING_TRACK_MARKER_RE = re.compile(r"^\s*(?:\d{1,2}[-.])?(\d{1,2})[\s._-]+")

# Fallback for a filename with no clean leading marker — a standalone 1-2
# digit token anywhere in the name (e.g. "Artist_Album_05_Title").
_EMBEDDED_TRACK_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,2})(?!\d)")

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _strip_known_prefix(stem: str, value: str | None) -> str:
    """Strips `value` (the wanted item's artist or album) off the front of
    `stem` if it's there, comparing letters/digits only, case-insensitive
    — a filename can't literally contain whatever MusicBrainz calls
    something if that includes a character Windows forbids in a path
    ("*NSYNC" shows up on disk as "-NSYNC", "NSYNC", etc.), so comparing
    the literal strings would silently fail to recognize the very prefix
    it's meant to strip. A no-op (returns stem unchanged) if `value`
    isn't actually a prefix of stem."""
    if not value:
        return stem
    target = _NON_ALNUM_RE.sub("", value.lower())
    if not target:
        return stem
    normalized = ""
    cut = 0
    for i, ch in enumerate(stem):
        if ch.isalnum():
            normalized += ch.lower()
        cut = i + 1
        if normalized == target:
            break
    else:
        return stem
    if normalized != target:
        return stem
    remainder = stem[cut:]
    sep = re.match(r"[\s._-]+", remainder)
    return remainder[sep.end():] if sep else remainder


def _strip_repeated_prefix(filename: str, artist: str, album: str | None) -> str:
    """Some rips repeat "Artist - Album - " (or just "Artist - ") on every
    filename in the batch, ahead of the actual track marker — strip that
    off first so the marker-parsing below still finds it leading. A no-op
    when the filename doesn't actually have that prefix."""
    stem = PureWindowsPath(filename).stem
    stem = _strip_known_prefix(stem, artist)
    stem = _strip_known_prefix(stem, album)
    return stem


def extract_track_number(filename: str, artist: str = "", album: str | None = None) -> int | None:
    stem = _strip_repeated_prefix(filename, artist, album)
    match = _LEADING_TRACK_MARKER_RE.match(stem)
    if match:
        return int(match.group(1))
    match = _EMBEDDED_TRACK_NUMBER_RE.search(stem)
    return int(match.group(1)) if match else None


def extract_track_title(filename: str, artist: str, album: str | None = None) -> str | None:
    """Best-effort track title guessed from the filename, used to match
    against MusicBrainz's tracklist by title instead of by position.

    Position-only matching ties tagging to whichever specific release
    edition happened to come back from the MusicBrainz search — for an
    album with many regional/bonus-track pressings (a common case), that
    edition's track count and order won't necessarily line up with what
    a given Soulseek peer actually has, silently mislabeling tracks.
    Matching by title instead works regardless of which edition's
    tracklist we're comparing against, since a bonus-track edition adds
    tracks rather than renaming the ones a plainer rip already has."""
    stem = _strip_repeated_prefix(filename, artist, album)
    match = _LEADING_TRACK_MARKER_RE.match(stem)
    title = stem[match.end():] if match else stem
    return title.strip() or None


def normalize_title(title: str | None) -> str:
    """Letters/digits only, case-insensitive — tolerant of punctuation and
    minor formatting differences between two titles for the same track
    (curly vs straight apostrophes, "&" vs "and", extra whitespace)."""
    return _NON_ALNUM_RE.sub("", (title or "").lower())

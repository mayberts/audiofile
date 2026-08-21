from __future__ import annotations

import logging
import re
from pathlib import PureWindowsPath

from sqlmodel import Session, select

from ..clients.slskd import SlskdClient, SlskdError
from ..config import Settings
from ..models import DownloadRecord, DownloadStatus, WantedItem, WantedStatus
from . import search as search_service

logger = logging.getLogger(__name__)

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


def _extract_track_number(filename: str, artist: str = "", album: str | None = None) -> int | None:
    stem = _strip_repeated_prefix(filename, artist, album)
    match = _LEADING_TRACK_MARKER_RE.match(stem)
    if match:
        return int(match.group(1))
    match = _EMBEDDED_TRACK_NUMBER_RE.search(stem)
    return int(match.group(1)) if match else None


def _extract_track_title(filename: str, artist: str, album: str | None = None) -> str | None:
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


# Soulseek uploads come almost exclusively from Windows clients, so a
# folder/file name can never literally contain a character Windows forbids
# in paths — an official title that does (MusicBrainz lists NSYNC's debut
# as "*NSYNC"; no real upload can be named that, it shows up as "-NSYNC",
# "NSYNC", etc.) needs that character stripped before it's used as a search
# term, or slskd's substring search is asking for text no real file has.
_WINDOWS_INVALID_PATH_CHARS_RE = re.compile(r'[<>:"/\\|?*]')


def _search_term(text: str) -> str:
    # split()+join() rather than a plain strip() collapses the run of
    # whitespace left behind when a stripped character sat mid-word
    # ("AC/DC" -> "AC DC", not "AC  DC").
    return " ".join(_WINDOWS_INVALID_PATH_CHARS_RE.sub(" ", text).split())


def _build_query(item: WantedItem) -> str:
    artist = _search_term(item.artist)
    if item.track:
        return f"{artist} {_search_term(item.track)}"
    if item.album:
        return f"{artist} {_search_term(item.album)}"
    return artist


def process_wanted_item(
    session: Session, item: WantedItem, slskd: SlskdClient, settings: Settings
) -> None:
    if item.status in (WantedStatus.SEARCHING, WantedStatus.DOWNLOADING):
        # Already in flight — the background scan (every
        # wanted_scan_interval_minutes) and the manual "Scan" button both
        # funnel through here with no locking between them, so without this
        # a double-click, or a manual scan landing right as the scheduled
        # tick picks up the same item, searches and enqueues the same files
        # twice. slskd doesn't dedupe that itself — it just saves the second
        # copy with a "_dup" suffix.
        logger.info("wanted item %s already %s, skipping duplicate scan", item.id, item.status)
        return

    item.status = WantedStatus.SEARCHING
    session.add(item)
    session.commit()

    try:
        # This runs in the background (scheduler tick or "Scan All Now"), not
        # blocking a page load, so it can afford to wait longer than the
        # interactive search page for slower-to-respond Soulseek peers. 45s
        # turned out not to be nearly enough for heavily-shared content —
        # a popular album can still be picking up its first handful of the
        # (eventual) hundred-plus responses well past that mark, so the scan
        # gave up and reported "not found" even though the exact same query,
        # given a couple more minutes, found plenty.
        raw = slskd.search(_build_query(item), timeout_ms=120000)
    except SlskdError as exc:
        logger.warning("search failed for wanted item %s: %s", item.id, exc)
        item.status = WantedStatus.FAILED
        item.last_error = str(exc)
        session.add(item)
        session.commit()
        return

    results = search_service.parse_search_responses(raw)
    logger.info(
        "wanted item %s: %d raw peer responses, %d audio-file results after parsing",
        item.id,
        len(raw),
        len(results),
    )

    # An album want should grab everything one peer has in one folder, not
    # just the single highest-scored file across everyone — that's what was
    # producing one random track instead of the whole album. Only applies
    # when there's no specific track (a genuine single-track want still
    # wants exactly one file), and falls back to the old single-file match
    # if nobody has at least 2 tracks of it in one folder.
    is_album_want = bool(item.album) and not item.track
    matches: list[search_service.SearchFile] = []
    if is_album_want:
        folder = search_service.best_album_folder(results, settings)
        if folder:
            matches = folder
    if not matches:
        single = search_service.best_match(results, settings)
        if single:
            matches = [single]

    if not matches:
        item.status = WantedStatus.NOT_FOUND
        item.last_error = "no matching files found on Soulseek"
        session.add(item)
        session.commit()
        return

    username = matches[0].username
    is_batch = len(matches) > 1

    # Persisted BEFORE telling slskd to start the transfer, deliberately —
    # slskd runs the download independently of our own process once it's
    # told to start, so if audiofile itself gets interrupted (a restart, a
    # crash) anywhere between the enqueue call succeeding and our own
    # commit, slskd finishes the download on its own with our database
    # never having recorded it existed at all: no DownloadRecord means
    # nothing ever picks it up for tagging or moving into the library,
    # no matter how long it sits there fully downloaded. Committing these
    # first means the worst case if something goes wrong right after is a
    # handful of QUEUED records slskd never actually started — visible and
    # retryable — instead of a real transfer with zero trace of it here.
    records: list[DownloadRecord] = []
    for m in matches:
        record = DownloadRecord(
            wanted_item_id=item.id,
            slskd_username=username,
            slskd_filename=m.filename,
            size_bytes=m.size,
            hint_artist=item.artist,
            hint_album=item.album,
            # A whole-folder grab has no single track name from the wanted
            # item itself — hint_track is instead a best-effort title
            # guessed from the filename, with hint_track_number as a
            # fallback for when that guess doesn't match anything.
            hint_track=item.track if not is_batch else _extract_track_title(m.filename, item.artist, item.album),
            hint_track_number=_extract_track_number(m.filename, item.artist, item.album) if is_batch else None,
            status=DownloadStatus.QUEUED,
        )
        session.add(record)
        records.append(record)

    item.status = WantedStatus.DOWNLOADING
    item.last_error = None
    session.add(item)
    session.commit()

    try:
        slskd.enqueue_download(username, [{"filename": m.filename, "size": m.size} for m in matches])
    except SlskdError as exc:
        for record in records:
            record.status = DownloadStatus.FAILED
            record.error = str(exc)
            session.add(record)
        item.status = WantedStatus.FAILED
        item.last_error = str(exc)
        session.add(item)
        session.commit()


def process_all_wanted(session: Session, slskd: SlskdClient, settings: Settings) -> int:
    # NOT_FOUND is retried alongside WANTED, not just skipped forever: Soulseek
    # is a live P2P network, so "no matching files" from one search is often
    # just whichever peers happened to be online at that moment, not proof the
    # content isn't out there. FAILED (a real error, e.g. slskd unreachable)
    # and anything already downloading/done are left alone.
    items = session.exec(
        select(WantedItem).where(WantedItem.status.in_([WantedStatus.WANTED, WantedStatus.NOT_FOUND]))
    ).all()
    for item in items:
        process_wanted_item(session, item, slskd, settings)
    return len(items)

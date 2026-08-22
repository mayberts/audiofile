from __future__ import annotations

import logging
import re

from sqlmodel import Session, select, update

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.slskd import SlskdClient, SlskdError
from ..config import Settings
from ..models import DownloadRecord, DownloadStatus, WantedItem, WantedStatus
from . import search as search_service
from .track_parsing import extract_track_number as _extract_track_number
from .track_parsing import extract_track_title as _extract_track_title

logger = logging.getLogger(__name__)

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
    session: Session, item: WantedItem, slskd: SlskdClient, settings: Settings, mb: MusicBrainzClient
) -> None:
    # Claim the item atomically before doing anything else: the background
    # scan (every wanted_scan_interval_minutes, its own APScheduler thread)
    # and the manual "Scan" button (a request-handling thread, via
    # scan-now) both funnel through here on independent DB sessions, with
    # nothing else serializing them. slskd doesn't dedupe a double-enqueue
    # of the same files — it just saves the second copy with a "_dup"
    # suffix once our own organizer detects the destination collision.
    #
    # A plain "if item.status in (...): return" followed by a separate
    # "set SEARCHING, commit" leaves a window where both callers read the
    # same pre-scan status before either commits — so both proceed to
    # search and enqueue the very same files. A single atomic
    # UPDATE ... WHERE status NOT IN (...) closes that window: only
    # whichever caller's UPDATE actually lands first affects a row (SQLite
    # serializes concurrent writers itself), so the loser sees rowcount==0
    # and backs off instead of proceeding. Verified under a concurrent-
    # thread stress test — two threads racing this exact claim against the
    # same row, real search results, hundreds of trials — that this alone
    # is enough to keep enqueue_download from ever firing twice for one
    # scan attempt.
    result = session.exec(
        update(WantedItem)
        .where(
            WantedItem.id == item.id,
            WantedItem.status.not_in([WantedStatus.SEARCHING, WantedStatus.DOWNLOADING]),
        )
        .values(status=WantedStatus.SEARCHING)
    )
    session.commit()
    claimed_rowcount = result.rowcount

    if claimed_rowcount == 0:
        logger.info("wanted item %s already in flight, skipping duplicate scan", item.id)
        return
    session.refresh(item)

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
        # A release picked by hand (see the release-editions picker) has a
        # known, exact tracklist -- without this, "most tracks wins" always
        # preferred a peer sharing a big deluxe/extended-mix reissue over
        # one sharing exactly the edition that was actually picked, no
        # matter how deliberately it was chosen. Matching by title (not
        # just comparing folder sizes) also handles a single peer's folder
        # that bundles the plain tracks together with a full set of bonus/
        # extended-mix versions -- there's no smaller folder to prefer
        # over it in that case, so the titles themselves are what narrow
        # it down to just the tracks actually wanted.
        expected_titles: list[str] = []
        if item.release_mbid:
            try:
                pinned_release = mb.get_release(item.release_mbid)
                if pinned_release and pinned_release.tracks:
                    expected_titles = [t.get("title") or "" for t in pinned_release.tracks]
            except Exception:  # noqa: BLE001
                logger.warning(
                    "could not look up pinned release %s for wanted item %s; falling back to "
                    "most-tracks-wins folder selection",
                    item.release_mbid,
                    item.id,
                )
        folder = search_service.best_album_folder(
            results, settings, expected_titles=expected_titles, artist=item.artist, album=item.album
        )
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
            hint_release_mbid=item.release_mbid,
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


def process_all_wanted(session: Session, slskd: SlskdClient, settings: Settings, mb: MusicBrainzClient) -> int:
    # NOT_FOUND is retried alongside WANTED, not just skipped forever: Soulseek
    # is a live P2P network, so "no matching files" from one search is often
    # just whichever peers happened to be online at that moment, not proof the
    # content isn't out there. FAILED (a real error, e.g. slskd unreachable)
    # and anything already downloading/done are left alone.
    items = session.exec(
        select(WantedItem).where(WantedItem.status.in_([WantedStatus.WANTED, WantedStatus.NOT_FOUND]))
    ).all()
    for item in items:
        process_wanted_item(session, item, slskd, settings, mb)
    return len(items)

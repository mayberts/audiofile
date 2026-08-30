from __future__ import annotations

import json
import logging
import re

from sqlmodel import Session, select, update

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.slskd import SlskdClient, SlskdError
from ..config import Settings
from ..models import DownloadRecord, DownloadStatus, WantedItem, WantedReviewCandidate, WantedStatus
from . import search as search_service
from .track_parsing import extract_track_number as _extract_track_number
from .track_parsing import extract_track_title as _extract_track_title

# Broader ladder rungs are only reached after a narrower one already came up
# with no viable candidate, so they lean on cast-a-wider-net response volume
# rather than needing the full patience of the first, most-specific query.
_FIRST_RUNG_TIMEOUT_MS = 120000
_LATER_RUNG_TIMEOUT_MS = 45000
# Cap on how many scored candidates get pooled for manual review -- enough
# to give a real choice without dumping every long-tail scraped result on
# someone.
_MAX_POOLED_CANDIDATES = 5

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


def _query_ladder(item: WantedItem, year: str | None) -> list[str]:
    """Progressively broader queries to try within one scan attempt, most
    specific first. process_wanted_item stops at the first rung whose
    results produce any candidate folder at all, only moving on to a
    broader query when a narrower one comes up completely empty -- exact
    substring search on Soulseek's end means a slightly-off year or
    subtitle in the query can hide results a broader query would still
    find, and score_album_candidates (not the search text) is what
    actually judges whether a given folder is the right one, so casting a
    wider net doesn't come at the cost of accuracy.

    Only really "ladders" for an album want -- a track or artist-only want
    has nothing meaningfully broader to fall back to."""
    artist = _search_term(item.artist)
    if item.track:
        return [f"{artist} {_search_term(item.track)}"]
    if not item.album:
        return [artist]

    album = _search_term(item.album)
    rungs = [f"{artist} {album} {year}"] if year else []
    rungs.append(f"{artist} {album}")
    rungs.append(artist)

    # De-dupe consecutive identical rungs (e.g. no year resolved, so the
    # first rung is never built) while preserving order.
    deduped: list[str] = []
    for q in rungs:
        if not deduped or deduped[-1] != q:
            deduped.append(q)
    return deduped


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

    # An album want should grab everything one peer has in one folder, not
    # just the single highest-scored file across everyone — that's what was
    # producing one random track instead of the whole album. Only applies
    # when there's no specific track (a genuine single-track want still
    # wants exactly one file).
    is_album_want = bool(item.album) and not item.track

    release_tracks: list[dict] | None = None
    expected_track_count: int | None = None
    year: str | None = None
    if is_album_want:
        # Resolved BEFORE searching -- score_album_candidates uses the
        # tracklist for its confidence signal and the track count for its
        # coherence signal, and the release date seeds the year-qualified
        # first rung of the query ladder. A release picked by hand (via the
        # release-editions picker) is always authoritative; otherwise this
        # is a best-effort guess from artist/album text, same lookup
        # resolve_track_metadata (services/downloads.py) does at import
        # time. Failure degrades gracefully to neutral scoring defaults and
        # a shorter ladder -- logged loudly rather than swallowed quietly,
        # since a silent failure here is indistinguishable from "audiofile
        # just doesn't know anything about this release" from the outside.
        try:
            if item.release_mbid:
                release = mb.get_release(item.release_mbid)
            else:
                guess = mb.search_release(item.artist, item.album)
                release = mb.get_release(guess.release_mbid) if guess else None
        except Exception:
            logger.exception(
                "could not resolve a release for wanted item %s (%s - %s); scoring will use "
                "neutral defaults for the tracklist/year signals",
                item.id,
                item.artist,
                item.album,
            )
            release = None

        if release and release.tracks:
            release_tracks = release.tracks
            expected_track_count = len(release_tracks)
        if release and release.date:
            year = release.date[:4]

    matches: list[search_service.SearchFile] = []
    results: list[search_service.SearchFile] = []
    candidates: list[search_service.ScoredFolder] = []

    if is_album_want:
        ladder = _query_ladder(item, year)
        for rung_index, query in enumerate(ladder):
            # This runs in the background (scheduler tick or "Scan All Now"),
            # not blocking a page load, so the first (most specific) rung can
            # afford to wait the full 120s for slower-to-respond Soulseek
            # peers -- 45s turned out not to be nearly enough for heavily-
            # shared content, where a popular album can still be picking up
            # its first handful of the (eventual) hundred-plus responses well
            # past that mark. Later, broader rungs are only reached after a
            # narrower one already came up empty, and tend to get faster,
            # heavier response volume, so they use a shorter timeout.
            timeout_ms = _FIRST_RUNG_TIMEOUT_MS if rung_index == 0 else _LATER_RUNG_TIMEOUT_MS
            try:
                raw = slskd.search(query, timeout_ms=timeout_ms)
            except SlskdError as exc:
                logger.warning("search failed for wanted item %s (query %r): %s", item.id, query, exc)
                item.status = WantedStatus.FAILED
                item.last_error = str(exc)
                session.add(item)
                session.commit()
                return

            results = search_service.parse_search_responses(raw)
            candidates = search_service.score_album_candidates(
                results,
                settings,
                item.artist,
                item.album,
                expected_track_count=expected_track_count,
                release_tracks=release_tracks,
            )
            logger.info(
                "wanted item %s: ladder rung %d/%d (%r) -> %d raw peer responses, %d audio-file "
                "results, %d candidate folder(s)",
                item.id,
                rung_index + 1,
                len(ladder),
                query,
                len(raw),
                len(results),
                len(candidates),
            )
            if candidates:
                # Something cleared the folder-grouping floor (min_tracks) at
                # this rung -- stop escalating. A broader query beyond this
                # point widens the search text, not the judgment of whether a
                # given folder is actually the right one (that's
                # score_album_candidates' job, not the query's), so there's
                # nothing left to gain from trying an even broader rung.
                break

        if candidates:
            top = candidates[0]
            if top.tier == "auto":
                matches = top.files
            elif top.tier == "manual":
                _pool_review_candidates(session, item, candidates)
                return
            # top.tier == "rejected" -> matches stays empty, falls through to
            # the single-file best_match fallback below, same as an album
            # want has always done when nothing folder-shaped panned out.
    else:
        try:
            raw = slskd.search(_build_query(item), timeout_ms=_FIRST_RUNG_TIMEOUT_MS)
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

    _enqueue_matches(session, item, matches, slskd)


def _pool_review_candidates(
    session: Session, item: WantedItem, candidates: list[search_service.ScoredFolder]
) -> None:
    """Persists the top scored candidates (see score_album_candidates) for a
    human to pick from via the /api/wanted/{id}/candidates endpoints,
    instead of auto-enqueueing anything -- reached when the best candidate's
    tier is "manual": nothing scored confidently enough to trust
    unattended, but there's at least one folder that plausibly could be
    this album. rejected-tier candidates are left out of the pool entirely
    -- they're not worth showing as an option.

    Clears any rows already pooled for this item first -- process_wanted_item's
    claim only excludes SEARCHING/DOWNLOADING, not AWAITING_REVIEW, so a
    manual re-scan of an item already sitting in review is possible (the UI
    doesn't offer it, but the endpoint doesn't forbid it either) and would
    otherwise leave a stale first batch sitting alongside the new one."""
    stale = session.exec(
        select(WantedReviewCandidate).where(WantedReviewCandidate.wanted_item_id == item.id)
    ).all()
    for s in stale:
        session.delete(s)

    pooled = [c for c in candidates if c.tier in ("auto", "manual")][:_MAX_POOLED_CANDIDATES]
    for c in pooled:
        session.add(
            WantedReviewCandidate(
                wanted_item_id=item.id,
                username=c.username,
                directory=c.directory,
                file_count=len(c.files),
                total_size_bytes=sum(f.size for f in c.files),
                score=c.score,
                tier=c.tier,
                files_json=json.dumps(
                    [
                        {"filename": f.filename, "size": f.size, "bitrate": f.bitrate, "extension": f.extension}
                        for f in c.files
                    ]
                ),
            )
        )
    item.status = WantedStatus.AWAITING_REVIEW
    item.last_error = None
    session.add(item)
    session.commit()
    logger.info("wanted item %s: %d candidate(s) pooled for manual review", item.id, len(pooled))


def _enqueue_matches(
    session: Session, item: WantedItem, matches: list[search_service.SearchFile], slskd: SlskdClient
) -> None:
    """Builds one DownloadRecord per matched file and tells slskd to start
    the transfer. Shared by process_wanted_item's automatic path and the
    manual-review "pick a candidate" endpoint (routers/wanted.py) so both
    ways of deciding what to grab go through the exact same persist-before-
    enqueue sequencing and hint-building logic."""
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

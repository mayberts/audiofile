from __future__ import annotations

import concurrent.futures
import difflib
import json
import logging
import re
from datetime import datetime

from plexapi.server import PlexServer
from sqlmodel import Session, func, select, update

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.plex import PlexNotConfigured, get_artist_album_titles, get_artist_item, get_plex_server
from ..config import get_settings
from ..models import AlbumTrackGap, LibraryAlbum, TrackGapScan, TrackGapScanStatus

logger = logging.getLogger(__name__)

# Secondary/live/compilation-style release groups are rarely what someone
# means by "albums I don't have", so keep the default check to primary
# studio albums only.
SKIP_SECONDARY_TYPES = {"Live", "Compilation", "Remix", "DJ-mix", "Mixtape/Street", "Demo"}

# MusicBrainz release-group titles and Plex's own album titles frequently
# differ in ways that don't reflect a real difference in the album: a
# curly vs. straight apostrophe, or a MusicBrainz release group carrying an
# edition suffix ("Thriller (Special Edition)") for an album Plex just has
# tagged as the plain title. Stripping both down before comparing avoids
# flagging albums the user already owns as missing.
#
# "live" is included here too -- MusicBrainz routinely tags *every* track
# on a live release with a redundant "(live)"/"(live at ...)" qualifier
# ("Ambitionz az a Ridah (live)") that a Plex tag for the same file usually
# omits (the whole album already being a live recording makes it
# redundant), which otherwise fails every single track on the release to
# match and reports the entire album missing.
_EDITION_SUFFIX_RE = re.compile(
    r"[\(\[][^)\]]*\b(deluxe|remaster(ed)?|bonus(\s*tracks?)?|special\s*edition|"
    r"anniversary|expanded|reissue|explicit|edition|live)\b[^)\]]*[\)\]]",
    re.IGNORECASE,
)
# Hyphens/dashes act as word separators ("Self-Destruct" vs "Self Destruct"
# vs "Self–Destruct" are all the same words to a listener) so they become a
# space rather than being deleted outright — deleting them would otherwise
# collapse "Self-Destruct" into the single word "selfdestruct", which no
# longer matches a Plex tag spelled with a plain space.
_HYPHEN_RE = re.compile("[-‐‑‒–—―]")
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_title(title: str) -> str:
    title = _EDITION_SUFFIX_RE.sub("", title)
    title = _HYPHEN_RE.sub(" ", title)
    title = _NON_WORD_RE.sub("", title)
    return _WHITESPACE_RE.sub(" ", title).strip().lower()


def _title_variants(title: str) -> set[str]:
    """The full normalized title, plus (if the title has a ": subtitle"
    suffix) just the part before the colon — MusicBrainz sometimes carries
    the full official title ("Animal Ambition: An Untamed Desire to Win")
    for an album Plex just has tagged with the short marketing title
    ("Animal Ambition")."""
    variants = {_normalize_title(title)}
    if ":" in title:
        variants.add(_normalize_title(title.split(":", 1)[0]))
    return variants


def _is_studio_album(rg: dict) -> bool:
    secondary = set(rg.get("secondary-types", []))
    return not (secondary & SKIP_SECONDARY_TYPES)


# Below this, exact-normalized-title matching stops covering real-world
# variance that isn't a structural edition/live difference: MusicBrainz and
# a Plex tag routinely just spell the same track differently -- "Optigan I"
# vs "Optigan 1", "Pt. 1" vs "Part 1", "Lookin'" vs "Looking", "There's" vs
# "There Is". A close-text similarity fallback catches these without
# needing its own edit-stripping rule for every individual case.
#
# Deliberately NOT a substring-containment check ("is the shorter title
# contained in the longer one") even though that would also catch a
# shortened marketing title against MusicBrainz's fuller one -- a
# genuinely different, unowned version of a track routinely *is* the plain
# title plus an appended qualifier ("Hit 'Em Up" vs "Hit 'Em Up (single
# version)", confirmed for real by this album's own scan results), which
# containment can't tell apart from harmless wording drift. The ratio
# check below already fails that case on its own (a whole extra qualifier
# phrase pulls it well under the threshold), without needing a separate
# rule that would also swallow real gaps like that one.
_FUZZY_MATCH_MIN_RATIO = 0.87

# A trailing part/chapter/movement number is exactly the part of a title
# that must NOT be fuzzed over -- "Part 1" and "Part 2" (or "Optigan I" vs
# "Optigan II") share almost every character, so the ratio/substring checks
# below would otherwise happily call them the same track. Only trips when
# *both* titles end in one of these (so "Optigan I" -- no number on the
# Plex side -- still matches "Optigan 1" via the ratio check below).
_TRAILING_ORDINAL_RE = re.compile(r"(?:^|\s)(\d+|[ivxlcdm]{1,4})$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _roman_to_int(token: str) -> int | None:
    total = 0
    prev = 0
    for ch in reversed(token):
        value = _ROMAN_VALUES.get(ch)
        if value is None:
            return None
        total += -value if value < prev else value
        prev = max(prev, value)
    return total or None


def _trailing_ordinal_value(title_normalized: str) -> int | None:
    match = _TRAILING_ORDINAL_RE.search(title_normalized)
    if not match:
        return None
    token = match.group(1)
    return int(token) if token.isdigit() else _roman_to_int(token)


def _title_owned(title_normalized: str, owned_normalized: set[str]) -> bool:
    if title_normalized in owned_normalized:
        return True
    ordinal = _trailing_ordinal_value(title_normalized)
    for owned in owned_normalized:
        if ordinal is not None:
            owned_ordinal = _trailing_ordinal_value(owned)
            if owned_ordinal is not None and owned_ordinal != ordinal:
                continue
        if difflib.SequenceMatcher(None, title_normalized, owned).ratio() >= _FUZZY_MATCH_MIN_RATIO:
            return True
    return False


def _find_best_release(mb: MusicBrainzClient, artist: str, album: str, owned_normalized: set[str]):
    """Tries candidate editions of this album smallest-first (see
    MusicBrainzClient.search_release_candidates) and returns as soon as one
    fully accounts for every track already owned -- picking the biggest
    edition by default (the old behavior) meant a plain/standard copy got
    compared against a deluxe reissue's bonus tracks and had them reported
    as "missing" even though they were never part of what was owned.

    Falls back to whichever candidate best overlaps the library (by
    Jaccard similarity of normalized track titles) if none fully accounts
    for it -- there's a genuine gap either way at that point, so the
    closest-matching edition is still the most useful one to diff
    against."""
    candidates = mb.search_release_candidates(artist, album)
    best = None
    best_score = -1.0
    for candidate in candidates:
        full = mb.get_release(candidate["id"])
        if not full or not full.tracks:
            continue
        candidate_normalized = {_normalize_title(t.get("title") or "") for t in full.tracks}
        matched = sum(1 for owned in owned_normalized if _title_owned(owned, candidate_normalized))
        if owned_normalized and matched == len(owned_normalized):
            return full
        union = len(owned_normalized | candidate_normalized) or 1
        score = matched / union
        if score > best_score:
            best_score = score
            best = full
    return best


def get_missing_tracks_for_album(
    plex: PlexServer, mb: MusicBrainzClient, album_rating_key: str, release_mbid: str | None = None
) -> dict:
    """Compares the tracks Plex has for one album against MusicBrainz's
    canonical tracklist for that release — checked live, on demand, for just
    this one album (not a background scan), the same way missing-album
    checks work per-artist.

    A caller can pin an exact release_mbid (picked via a release search --
    see search_releases()) to compare against instead of leaving it to
    _find_best_release()'s guess. That's the only way to reach a
    bonus-disc/deluxe reissue MusicBrainz models as its own separate
    release with a different title ("Album: Side B") rather than another
    edition of the same release-group -- guessing from this album's own
    title would never find it."""
    album = plex.fetchItem(int(album_rating_key))
    owned_normalized = {_normalize_title(t.title) for t in album.tracks()}
    empty = {
        "checked": False,
        "expected_total": None,
        "owned_total": len(owned_normalized),
        "missing_tracks": [],
        "release_mbid": None,
        "release_title": None,
    }

    if release_mbid:
        full_release = mb.get_release(release_mbid)
    else:
        full_release = _find_best_release(mb, album.parentTitle, album.title, owned_normalized)

    if not full_release or not full_release.tracks:
        return empty

    missing = []
    for t in full_release.tracks:
        title = t.get("title") or ""
        if _title_owned(_normalize_title(title), owned_normalized):
            continue
        missing.append({"title": title, "track_number": t.get("position"), "disc": t.get("disc")})

    return {
        "checked": True,
        "expected_total": len(full_release.tracks),
        "owned_total": len(owned_normalized),
        "missing_tracks": missing,
        "release_mbid": full_release.release_mbid,
        "release_title": full_release.title,
    }


def get_owned_album_titles_from_snapshot(session: Session, artist_name: str) -> set[str]:
    """Cross-references against the persisted Plex library snapshot (see
    LibraryAlbum / /api/plex/library/scan) by artist name, not rating key —
    used by the free-text-artist discography picker on the Wanted page,
    which has no Plex rating key to look up (the artist name typed there
    may not even match a known Plex item, or the library may never have
    been scanned)."""
    rows = session.exec(
        select(LibraryAlbum).where(func.lower(LibraryAlbum.artist) == artist_name.strip().lower())
    ).all()
    return {_normalize_title(row.album) for row in rows}


def get_artist_discography(
    mb: MusicBrainzClient, artist_name: str, owned_normalized: set[str] | None = None
) -> list[dict]:
    """Every studio album MusicBrainz lists for this artist name, most
    recent first. Doesn't touch Plex at all, so it works for an artist you
    don't own anything by yet — used to let someone pick specific albums to
    want instead of adding one ambiguous "whole discography" entry.

    If owned_normalized is given, each album is tagged with whether it
    matches something already owned, rather than being filtered out —
    the caller decides whether "already have it" means hide or just flag."""
    mb_artist = mb.search_artist(artist_name)
    if not mb_artist:
        return []

    release_groups = mb.get_artist_release_groups(mb_artist["id"])
    albums = [
        {
            "album": rg.get("title", ""),
            "release_group_mbid": rg.get("id"),
            "first_release_date": rg.get("first-release-date"),
        }
        for rg in release_groups
        if _is_studio_album(rg)
    ]
    albums.sort(key=lambda a: a["first_release_date"] or "", reverse=True)
    for album in albums:
        album["in_library"] = bool(owned_normalized and (_title_variants(album["album"]) & owned_normalized))
    return albums


def get_missing_albums_for_artist(plex: PlexServer, mb: MusicBrainzClient, artist_rating_key: str) -> list[dict]:
    """Studio albums MusicBrainz lists for this artist that aren't already
    in the Plex library — checked live, just for this one artist (two
    MusicBrainz requests), not a whole-library background scan."""
    artist = get_artist_item(plex, artist_rating_key)
    owned_normalized = {_normalize_title(t) for t in get_artist_album_titles(artist)}
    discography = get_artist_discography(mb, artist.title, owned_normalized)
    return [a for a in discography if not a["in_library"]]


def run_track_gap_scan(scan_id: int) -> None:
    """Runs get_missing_tracks_for_album across every album in the
    persisted Plex snapshot (LibraryAlbum -- the same source of truth the
    rest of the app treats as "what's in your library", not a fresh live
    Plex walk), upserting/deleting AlbumTrackGap rows as it goes so the
    results page updates live instead of only once the whole thing (which
    can be thousands of albums) finishes.

    Checks run in a bounded thread pool sized to
    settings.musicbrainz_concurrent_requests -- MusicBrainzClient's own
    module-level semaphore (clients/musicbrainz.py) enforces the same
    limit against the actual HTTP requests regardless of how many workers
    this pool has, but sizing the pool to match avoids piling up worker
    threads that just sit blocked on that semaphore for no benefit. A
    fresh Session per album (not one held for the whole run) both lets
    concurrent workers commit independently and keeps every commit visible
    to the progress-polling GET endpoint immediately rather than only once
    the whole scan (possibly thousands of albums) finishes.

    Runs as a FastAPI BackgroundTasks target (see routers/track_gaps.py),
    so it owns its own settings/clients/sessions start to finish -- there's
    no request-scoped anything to inherit."""
    from ..database import engine

    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        try:
            plex = get_plex_server(settings)
        except PlexNotConfigured as exc:
            _finish_scan(scan_id, TrackGapScanStatus.FAILED, last_error=str(exc))
            return

        with Session(engine) as session:
            albums = session.exec(select(LibraryAlbum)).all()
            # Plain tuples, not the ORM rows themselves -- session.commit()
            # below expires every attribute on them, and this loop runs for
            # a long time across many more sessions after this one closes,
            # so touching an attribute on the ORM instance later raises
            # DetachedInstanceError instead of silently refetching.
            album_snapshots = [
                (a.rating_key, a.artist, a.album, a.thumb, a.pinned_release_mbid) for a in albums
            ]
            scan = session.get(TrackGapScan, scan_id)
            scan.total_albums = len(album_snapshots)
            session.add(scan)
            session.commit()

        def check_one(snapshot: tuple) -> None:
            rating_key, artist, album_title, thumb, pinned_release_mbid = snapshot
            try:
                result = get_missing_tracks_for_album(plex, mb, rating_key, pinned_release_mbid)
            except Exception:
                # One bad album (a stale rating_key, a transient MB error
                # that exhausted its own retries) shouldn't abort a scan
                # that might be hours into thousands of albums -- log it,
                # leave whatever AlbumTrackGap row already existed for it
                # alone, and move on.
                logger.exception(
                    "track gap scan %s: failed checking album %s (%s - %s)",
                    scan_id,
                    rating_key,
                    artist,
                    album_title,
                )
                result = None
            _persist_gap_result(engine, scan_id, rating_key, artist, album_title, thumb, result)

        max_workers = max(1, settings.musicbrainz_concurrent_requests)
        cancelled = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = []
            for snapshot in album_snapshots:
                if _scan_cancel_requested(engine, scan_id):
                    cancelled = True
                    break
                futures.append(pool.submit(check_one, snapshot))
            # Waits for whatever's already been submitted to finish before
            # this function (and the "with" block closing the pool) returns
            # -- there's no clean way to interrupt an in-flight
            # ThreadPoolExecutor task, so a cancellation still lets up to
            # max_workers already-started checks complete rather than
            # abandoning them mid-request.
            concurrent.futures.wait(futures)

        if cancelled or _scan_cancel_requested(engine, scan_id):
            _finish_scan(scan_id, TrackGapScanStatus.CANCELLED)
        else:
            _finish_scan(scan_id, TrackGapScanStatus.COMPLETED)
    finally:
        mb.close()


def _scan_cancel_requested(engine, scan_id: int) -> bool:
    with Session(engine) as session:
        scan = session.get(TrackGapScan, scan_id)
        return bool(scan and scan.cancel_requested)


def _persist_gap_result(
    engine,
    scan_id: int,
    rating_key: str,
    artist: str,
    album_title: str,
    thumb: str | None,
    result: dict | None,
) -> None:
    """Upserts/deletes this one album's AlbumTrackGap row and atomically
    bumps the scan's checked_albums counter. Safe to call from several
    threads at once for *different* albums (each rating_key is unique
    across the whole scan, so no two concurrent calls ever touch the same
    AlbumTrackGap row) -- checked_albums itself uses a SQL-level
    UPDATE ... SET checked_albums = checked_albums + 1 rather than a
    read-modify-write, since that part *is* shared across every worker and
    a plain "scan.checked_albums += 1; commit()" would lose updates under
    real concurrency (two workers both reading the same starting value)."""
    with Session(engine) as session:
        existing = session.exec(select(AlbumTrackGap).where(AlbumTrackGap.rating_key == rating_key)).first()

        if result and result["checked"] and result["missing_tracks"]:
            missing_titles = [t["title"] for t in result["missing_tracks"]]
            if existing:
                existing.artist = artist
                existing.album = album_title
                existing.thumb = thumb
                existing.expected_total = result["expected_total"]
                existing.owned_total = result["owned_total"]
                existing.missing_count = len(missing_titles)
                existing.missing_tracks_json = json.dumps(missing_titles)
                existing.release_title = result["release_title"]
                existing.checked_at = datetime.utcnow()
                session.add(existing)
            else:
                session.add(
                    AlbumTrackGap(
                        rating_key=rating_key,
                        artist=artist,
                        album=album_title,
                        thumb=thumb,
                        expected_total=result["expected_total"],
                        owned_total=result["owned_total"],
                        missing_count=len(missing_titles),
                        missing_tracks_json=json.dumps(missing_titles),
                        release_title=result["release_title"],
                    )
                )
        elif existing:
            # Complete now (or no longer resolvable on MusicBrainz) -- either
            # way it's not a gap anymore, don't leave a stale row claiming it
            # still is.
            session.delete(existing)

        session.exec(
            update(TrackGapScan)
            .where(TrackGapScan.id == scan_id)
            .values(checked_albums=TrackGapScan.checked_albums + 1)
        )
        session.commit()


def _finish_scan(scan_id: int, status: TrackGapScanStatus, last_error: str | None = None) -> None:
    from ..database import engine

    with Session(engine) as session:
        scan = session.get(TrackGapScan, scan_id)
        if scan is None:
            return
        scan.status = status
        scan.last_error = last_error
        scan.finished_at = datetime.utcnow()
        session.add(scan)
        session.commit()

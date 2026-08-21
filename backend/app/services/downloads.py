from __future__ import annotations

import logging
from pathlib import Path, PureWindowsPath

from sqlmodel import Session, select

from ..clients.musicbrainz import MusicBrainzClient, ReleaseMatch, TrackMetadata
from ..clients.slskd import SlskdClient, find_transfer
from ..config import Settings
from ..models import DownloadRecord, DownloadStatus, WantedItem, WantedStatus
from . import organizer, tagging

logger = logging.getLogger(__name__)


def remote_basename(remote_filename: str) -> str:
    # slskd file paths come from Windows Soulseek clients, so they use backslashes.
    return PureWindowsPath(remote_filename).name


def locate_downloaded_file(download_dir: str, remote_filename: str) -> Path | None:
    target_name = remote_basename(remote_filename)
    root = Path(download_dir)
    if not root.exists():
        return None
    candidates = sorted(
        root.rglob(target_name),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    return candidates[0] if candidates else None


def sync_transfer_status(session: Session, slskd: SlskdClient) -> None:
    """Pull live progress from slskd for every download we're tracking that isn't finished."""
    pending = session.query(DownloadRecord).filter(
        DownloadRecord.status.in_([DownloadStatus.QUEUED, DownloadStatus.IN_PROGRESS])
    ).all()
    if not pending:
        return

    try:
        all_downloads = slskd.get_all_downloads()
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not fetch slskd transfer list: %s", exc)
        return

    newly_failed: list[DownloadRecord] = []
    for record in pending:
        transfer = find_transfer(all_downloads, record.slskd_username, record.slskd_filename)
        if transfer is None:
            continue

        state = (transfer.get("state") or "").lower()
        percent = float(transfer.get("percentComplete") or 0.0)
        record.progress_percent = percent

        if "completed, succeeded" in state or state == "completed":
            record.status = DownloadStatus.COMPLETED
        elif "completed" in state and "succeeded" not in state:
            record.status = DownloadStatus.FAILED
            record.error = transfer.get("state")
            newly_failed.append(record)
        elif "inprogress" in state or "queued" in state:
            record.status = DownloadStatus.IN_PROGRESS

        session.add(record)
    session.commit()

    # A transfer that fails here (peer went offline, errored out, timed
    # out — anything slskd itself gives up on) never reaches COMPLETED, so
    # it never goes through process_completed_download — the only other
    # place that reflects a finished record back onto its wanted item.
    # Without this, the wanted item sits in DOWNLOADING forever even
    # though slskd already gave up on the transfer.
    for record in newly_failed:
        _sync_wanted_item(session, record)


def resolve_track_metadata(
    record: DownloadRecord,
    mb: MusicBrainzClient,
    release_cache: dict[tuple[str, str], ReleaseMatch | None] | None = None,
) -> TrackMetadata:
    if record.hint_album and record.hint_artist:
        # An album-batch download produces one DownloadRecord per track, all
        # sharing the same artist+album — without this cache, tagging a
        # 13-track album meant 26 serialized MusicBrainz round-trips (two
        # per track, throttled to ~1/sec) for what's really one release
        # lookup, which both took ages and made catching a MusicBrainz 503
        # partway through the batch (failing only some of the tracks) far
        # more likely than it needs to be.
        cache_key = (record.hint_artist, record.hint_album)
        if release_cache is not None and cache_key in release_cache:
            release = release_cache[cache_key]
        else:
            release = mb.search_release(record.hint_artist, record.hint_album)
            if release:
                # search_release only returns summary release info — no
                # per-track listing — so a second lookup is needed to
                # actually get tracks to match against.
                full_release = mb.get_release(release.release_mbid)
                if full_release:
                    release = full_release
            if release_cache is not None:
                release_cache[cache_key] = release

        if release:
            matching_track = None
            if record.hint_track:
                # Title match first: it's robust to which specific release
                # edition search_release happened to return, since an
                # album with many regional/bonus-track pressings (varying
                # track counts and ordering) still has this track under
                # the same title regardless of which pressing's tracklist
                # we're comparing against. Position doesn't have that
                # property — it silently mismatches whenever the matched
                # release's tracklist doesn't line up with what this peer
                # actually has.
                matching_track = next(
                    (t for t in release.tracks if t.get("title", "").lower() == record.hint_track.lower()),
                    None,
                )
            if matching_track is None and record.hint_track_number is not None:
                matching_track = next(
                    (t for t in release.tracks if t.get("position") == record.hint_track_number),
                    None,
                )

            fallback_title = record.hint_track or Path(remote_basename(record.slskd_filename)).stem
            title = matching_track["title"] if matching_track else fallback_title
            track_number = matching_track["position"] if matching_track else record.hint_track_number
            return TrackMetadata(
                artist=release.artist or record.hint_artist,
                album=release.title or record.hint_album,
                title=title,
                track_number=track_number,
                year=(release.date or "")[:4] or None,
                release_mbid=release.release_mbid,
                release_group_mbid=release.release_group_mbid,
            )

    if record.hint_artist and record.hint_track:
        rec_meta = mb.search_recording(record.hint_artist, record.hint_track)
        if rec_meta:
            return rec_meta

    # Fall back to whatever hints/filename we already have; better than nothing.
    fallback_title = record.hint_track or Path(remote_basename(record.slskd_filename)).stem
    return TrackMetadata(
        artist=record.hint_artist or "Unknown Artist",
        album=record.hint_album or "Unknown Album",
        title=fallback_title,
    )


def process_completed_download(
    session: Session,
    record: DownloadRecord,
    settings: Settings,
    mb: MusicBrainzClient,
    release_cache: dict[tuple[str, str], ReleaseMatch | None] | None = None,
) -> None:
    record.status = DownloadStatus.TAGGING
    session.add(record)
    session.commit()

    local_path = locate_downloaded_file(settings.download_dir, record.slskd_filename)
    if local_path is None:
        record.status = DownloadStatus.FAILED
        record.error = "downloaded file not found on disk"
        session.add(record)
        session.commit()
        _sync_wanted_item(session, record)
        return

    try:
        meta = resolve_track_metadata(record, mb, release_cache)
        cover_bytes = None
        if meta.release_mbid:
            cover_bytes = mb.get_cover_art(meta.release_mbid)
        if cover_bytes is None and meta.release_group_mbid:
            cover_bytes = mb.get_release_group_cover_art(meta.release_group_mbid)

        tagging.tag_file(local_path, meta, cover_bytes)

        destination = organizer.library_path_for(settings.library_dir, meta, local_path)
        final_path = organizer.move_into_library(local_path, destination)

        record.final_path = str(final_path)
        record.mbid = meta.release_mbid
        record.status = DownloadStatus.DONE
        record.progress_percent = 100.0
        record.error = None  # clear a stale error from an earlier failed attempt (e.g. a retry)
    except Exception as exc:  # noqa: BLE001
        logger.exception("post-processing failed for download %s", record.id)
        record.status = DownloadStatus.FAILED
        record.error = str(exc)

    session.add(record)
    session.commit()
    _sync_wanted_item(session, record)


def reconcile_stuck_wanted_items(session: Session) -> None:
    """Catches a wanted item left stuck in SEARCHING/DOWNLOADING because one
    of its download records already reached a terminal state without ever
    triggering _sync_wanted_item for it — e.g. records that failed before
    sync_transfer_status started calling it, or any other path that missed
    the sync. Runs every poll tick; a no-op once nothing's actually stuck."""
    stuck = session.exec(
        select(WantedItem).where(WantedItem.status.in_([WantedStatus.SEARCHING, WantedStatus.DOWNLOADING]))
    ).all()
    for wanted in stuck:
        record = session.exec(
            select(DownloadRecord).where(DownloadRecord.wanted_item_id == wanted.id)
        ).first()
        if record is not None:
            _sync_wanted_item(session, record)


def _sync_wanted_item(session: Session, record: DownloadRecord) -> None:
    """Reflects a finished download back onto its wanted-list entry, which
    otherwise stays stuck on DOWNLOADING forever — nothing else transitions
    it once the DownloadRecord itself reaches a terminal state.

    An album want can produce several DownloadRecords sharing one
    wanted_item_id (one whole-folder batch), so this waits until every
    sibling record has reached DONE or FAILED before touching the wanted
    item — otherwise the first track to finish would prematurely remove it
    while the rest of the album is still downloading."""
    if record.wanted_item_id is None:
        return
    wanted = session.get(WantedItem, record.wanted_item_id)
    if wanted is None:
        return

    siblings = session.exec(
        select(DownloadRecord).where(DownloadRecord.wanted_item_id == wanted.id)
    ).all()
    terminal = {DownloadStatus.DONE, DownloadStatus.FAILED}
    if not all(s.status in terminal for s in siblings):
        return

    succeeded = [s for s in siblings if s.status == DownloadStatus.DONE]
    if succeeded:
        # At least one real file landed — nothing left to search or retry
        # for, so it comes off the list entirely rather than sitting there
        # as a permanently-green row.
        session.delete(wanted)
    else:
        wanted.status = WantedStatus.FAILED
        failed = [s for s in siblings if s.status == DownloadStatus.FAILED]
        wanted.last_error = (
            failed[-1].error if len(failed) == 1 else f"all {len(failed)} tracks failed to download"
        )
        session.add(wanted)
    session.commit()

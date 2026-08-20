from __future__ import annotations

import logging
from pathlib import Path, PureWindowsPath

from sqlmodel import Session

from ..clients.musicbrainz import MusicBrainzClient, TrackMetadata
from ..clients.slskd import SlskdClient, find_transfer
from ..config import Settings
from ..models import DownloadRecord, DownloadStatus
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
        elif "inprogress" in state or "queued" in state:
            record.status = DownloadStatus.IN_PROGRESS

        session.add(record)
    session.commit()


def resolve_track_metadata(record: DownloadRecord, mb: MusicBrainzClient) -> TrackMetadata:
    if record.hint_album and record.hint_artist:
        release = mb.search_release(record.hint_artist, record.hint_album)
        if release:
            # search_release only returns summary release info — no per-track
            # listing — so a second lookup is needed to actually get tracks
            # to match hint_track against and to fill in track_number below.
            full_release = mb.get_release(release.release_mbid)
            if full_release:
                release = full_release
            matching_track = next(
                (
                    t
                    for t in release.tracks
                    if record.hint_track and t.get("title", "").lower() == record.hint_track.lower()
                ),
                None,
            )
            title = matching_track["title"] if matching_track else (record.hint_track or release.title)
            track_number = matching_track["position"] if matching_track else None
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
        return

    try:
        meta = resolve_track_metadata(record, mb)
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
    except Exception as exc:  # noqa: BLE001
        logger.exception("post-processing failed for download %s", record.id)
        record.status = DownloadStatus.FAILED
        record.error = str(exc)

    session.add(record)
    session.commit()

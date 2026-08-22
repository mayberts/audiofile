from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.plex import PlexNotConfigured, get_plex_server, refresh_music_library
from ..clients.slskd import SlskdClient, SlskdError
from ..config import get_settings
from ..database import get_session
from ..models import DownloadRecord, DownloadStatus
from ..schemas import DownloadOut
from ..services import downloads as downloads_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@router.get("", response_model=list[DownloadOut])
def list_downloads(session: Session = Depends(get_session)):
    records = session.exec(select(DownloadRecord).order_by(DownloadRecord.created_at.desc())).all()
    return records


@router.delete("/completed")
def clear_completed_downloads(session: Session = Depends(get_session)):
    """Downloads are kept as history rather than auto-removed, so this is
    the manual way to clear out finished (done/failed/cancelled) rows —
    anything still queued, in progress, or tagging is left alone."""
    terminal = [DownloadStatus.DONE, DownloadStatus.FAILED, DownloadStatus.CANCELLED]
    records = session.exec(select(DownloadRecord).where(DownloadRecord.status.in_(terminal))).all()
    for record in records:
        session.delete(record)
    session.commit()
    return {"cleared": len(records)}


@router.post("/{download_id}/retry", response_model=DownloadOut)
def retry_download(download_id: int, session: Session = Depends(get_session)):
    """For a failed post-processing step (tagging/organizing), not a failed
    transfer — the file already downloaded fine and is sitting in the
    download dir, so this just re-runs tagging/organizing against it
    instead of re-fetching anything from Soulseek. A transient MusicBrainz
    503 mid-batch is exactly the kind of failure this recovers from without
    the user needing to re-search."""
    record = session.get(DownloadRecord, download_id)
    if not record:
        raise HTTPException(status_code=404, detail="download not found")
    if record.status != DownloadStatus.FAILED:
        raise HTTPException(status_code=400, detail="only failed downloads can be retried")

    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        downloads_service.process_completed_download(session, record, settings, mb)
    finally:
        mb.close()
    session.refresh(record)

    # Same as the scheduled poll: a file only just landed in the library
    # folder doesn't make Plex aware of it by itself, and this retry path
    # organizes independently of that poll tick.
    if record.status == DownloadStatus.DONE:
        try:
            refresh_music_library(get_plex_server(settings))
        except PlexNotConfigured:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("failed to trigger Plex library refresh after retry")

    return record


@router.post("/{download_id}/cancel", response_model=DownloadOut)
def cancel_download(download_id: int, session: Session = Depends(get_session)):
    record = session.get(DownloadRecord, download_id)
    if not record:
        raise HTTPException(status_code=404, detail="download not found")

    settings = get_settings()
    slskd = SlskdClient.from_settings(settings)
    try:
        slskd.cancel_download(record.slskd_username, record.slskd_filename)
    except SlskdError:
        pass  # best-effort; still mark as cancelled locally
    finally:
        slskd.close()

    record.status = DownloadStatus.CANCELLED
    session.add(record)
    session.commit()
    session.refresh(record)
    return record

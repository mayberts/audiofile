from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..clients.slskd import SlskdClient, SlskdError
from ..config import get_settings
from ..database import get_session
from ..models import DownloadRecord, DownloadStatus
from ..schemas import DownloadOut

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

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..clients.slskd import SlskdClient, SlskdError
from ..config import get_settings
from ..database import get_session
from ..models import DownloadRecord, DownloadStatus
from ..schemas import DownloadOut, DownloadRequest, SearchRequest, SearchResponse
from ..services.search import parse_search_responses

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
def run_search(payload: SearchRequest):
    settings = get_settings()
    slskd = SlskdClient(settings)
    try:
        raw = slskd.search(payload.query, timeout_ms=payload.timeout_ms)
    except SlskdError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        slskd.close()

    results = parse_search_responses(raw)
    return SearchResponse(query=payload.query, results=results)


@router.post("/download", response_model=DownloadOut)
def download_result(payload: DownloadRequest, session: Session = Depends(get_session)):
    settings = get_settings()
    slskd = SlskdClient(settings)
    try:
        slskd.enqueue_download(payload.username, [{"filename": payload.filename, "size": payload.size}])
    except SlskdError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        slskd.close()

    record = DownloadRecord(
        wanted_item_id=payload.wanted_item_id,
        slskd_username=payload.username,
        slskd_filename=payload.filename,
        size_bytes=payload.size,
        hint_artist=payload.hint_artist,
        hint_album=payload.hint_album,
        hint_track=payload.hint_track,
        mbid=payload.mbid,
        status=DownloadStatus.QUEUED,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record

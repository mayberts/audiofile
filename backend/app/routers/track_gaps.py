from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from ..database import get_session
from ..models import AlbumTrackGap, TrackGapScan, TrackGapScanStatus
from ..schemas import AlbumTrackGapOut, TrackGapScanOut
from ..services.plex_gaps import run_track_gap_scan

router = APIRouter(prefix="/api/track-gaps", tags=["track-gaps"])


@router.get("/scan", response_model=TrackGapScanOut | None)
def get_scan_status(session: Session = Depends(get_session)):
    """The most recent scan run, whatever its status -- None if a
    full-library scan has never been started."""
    return session.exec(select(TrackGapScan).order_by(TrackGapScan.started_at.desc())).first()


@router.post("/scan", response_model=TrackGapScanOut)
def start_scan(background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    current = session.exec(
        select(TrackGapScan).where(TrackGapScan.status == TrackGapScanStatus.RUNNING)
    ).first()
    if current:
        # Already running -- return it as-is rather than starting a second,
        # overlapping scan that would just race the first over the same
        # AlbumTrackGap rows.
        return current

    scan = TrackGapScan(status=TrackGapScanStatus.RUNNING)
    session.add(scan)
    session.commit()
    session.refresh(scan)

    background_tasks.add_task(run_track_gap_scan, scan.id)
    return scan


@router.post("/scan/cancel", response_model=TrackGapScanOut | None)
def cancel_scan(session: Session = Depends(get_session)):
    current = session.exec(
        select(TrackGapScan).where(TrackGapScan.status == TrackGapScanStatus.RUNNING)
    ).first()
    if not current:
        raise HTTPException(status_code=404, detail="no scan is currently running")
    # Cooperative -- the running scan checks this once per album (see
    # run_track_gap_scan), not killed outright.
    current.cancel_requested = True
    session.add(current)
    session.commit()
    session.refresh(current)
    return current


@router.get("", response_model=list[AlbumTrackGapOut])
def list_track_gaps(session: Session = Depends(get_session)):
    rows = session.exec(select(AlbumTrackGap).order_by(AlbumTrackGap.artist, AlbumTrackGap.album)).all()
    return [
        AlbumTrackGapOut(
            rating_key=row.rating_key,
            artist=row.artist,
            album=row.album,
            thumb=row.thumb,
            expected_total=row.expected_total,
            owned_total=row.owned_total,
            missing_count=row.missing_count,
            missing_tracks=json.loads(row.missing_tracks_json),
            release_mbid=row.release_mbid,
            release_title=row.release_title,
            checked_at=row.checked_at,
        )
        for row in rows
    ]

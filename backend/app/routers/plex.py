from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.plex import PlexNotConfigured
from ..config import get_settings
from ..database import get_session
from ..models import PlexMissingAlbum, WantedItem, WantedSource
from ..schemas import PlexGapOut
from ..services.plex_gaps import scan_for_gaps

router = APIRouter(prefix="/api/plex", tags=["plex"])


@router.post("/scan")
def trigger_scan(limit_artists: int | None = None, session: Session = Depends(get_session)):
    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        missing = scan_for_gaps(session, settings, mb, limit_artists=limit_artists)
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        mb.close()
    return {"new_missing_albums": missing}


@router.get("/gaps", response_model=list[PlexGapOut])
def list_gaps(session: Session = Depends(get_session)):
    return session.exec(
        select(PlexMissingAlbum).order_by(PlexMissingAlbum.artist, PlexMissingAlbum.album)
    ).all()


@router.post("/gaps/{gap_id}/add-to-wanted", response_model=PlexGapOut)
def add_gap_to_wanted(gap_id: int, session: Session = Depends(get_session)):
    gap = session.get(PlexMissingAlbum, gap_id)
    if not gap:
        raise HTTPException(status_code=404, detail="gap not found")

    wanted = WantedItem(
        artist=gap.artist,
        album=gap.album,
        release_group_mbid=gap.release_group_mbid,
        source=WantedSource.PLEX_GAP,
    )
    session.add(wanted)
    gap.added_to_wanted = True
    session.add(gap)
    session.commit()
    session.refresh(gap)
    return gap

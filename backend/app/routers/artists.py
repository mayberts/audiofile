from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from ..database import get_session
from ..models import TrackedArtist
from ..schemas import TrackArtistRequest, TrackedArtistOut

router = APIRouter(prefix="/api/artists", tags=["artists"])


@router.get("", response_model=list[TrackedArtistOut])
def list_tracked_artists(session: Session = Depends(get_session)):
    return session.exec(select(TrackedArtist).order_by(TrackedArtist.artist)).all()


@router.post("", response_model=TrackedArtistOut)
def track_artist(payload: TrackArtistRequest, session: Session = Depends(get_session)):
    """Adds an artist to the Library page purely for browsing -- lets
    ArtistDetailPage show their MusicBrainz discography even though nothing
    by them is owned yet. Deliberately doesn't touch the wanted list or
    kick off any search/download; that's a separate, explicit step."""
    name = payload.artist.strip()
    if not name:
        raise HTTPException(status_code=400, detail="artist name is required")

    existing = session.exec(
        select(TrackedArtist).where(func.lower(TrackedArtist.artist) == name.lower())
    ).first()
    if existing:
        return existing

    row = TrackedArtist(artist=name)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.delete("/{artist}", status_code=204)
def untrack_artist(artist: str, session: Session = Depends(get_session)):
    for row in session.exec(
        select(TrackedArtist).where(func.lower(TrackedArtist.artist) == artist.strip().lower())
    ).all():
        session.delete(row)
    session.commit()

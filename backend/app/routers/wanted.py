from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlmodel import Session, select

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.slskd import SlskdClient
from ..config import get_settings
from ..database import get_session
from ..models import WantedItem
from ..schemas import MissingAlbumOut, WantedCreate, WantedOut
from ..services.plex_gaps import get_artist_discography
from ..services.wanted import process_all_wanted, process_wanted_item

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wanted", tags=["wanted"])


@router.get("", response_model=list[WantedOut])
def list_wanted(session: Session = Depends(get_session)):
    return session.exec(select(WantedItem).order_by(WantedItem.created_at.desc())).all()


@router.get("/discography", response_model=list[MissingAlbumOut])
def get_discography(artist: str):
    """Backs the album picker shown when adding just an artist name (no
    album/track) — lets someone choose specific albums instead of one
    ambiguous "whole discography" wanted item. Doesn't need Plex at all."""
    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        return get_artist_discography(mb, artist)
    except httpx.HTTPStatusError as exc:
        logger.warning("Discography lookup failed for %r: %s", artist, exc)
        if exc.response.status_code == 503:
            raise HTTPException(
                status_code=503, detail="MusicBrainz is temporarily unavailable — try again in a moment."
            ) from exc
        raise HTTPException(
            status_code=502, detail=f"MusicBrainz returned an error ({exc.response.status_code})."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Discography lookup failed for %r", artist)
        raise HTTPException(status_code=502, detail=f"Could not check MusicBrainz: {exc}") from exc
    finally:
        mb.close()


@router.get("/cover-art/{release_group_mbid}")
def get_cover_art(release_group_mbid: str):
    """Proxies Cover Art Archive art for the discography picker — same
    "server fetches, browser only sees our URL" pattern as the Plex image
    proxy, and lets the browser cache it by URL across renders."""
    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        image_bytes = mb.get_release_group_cover_art(release_group_mbid)
    finally:
        mb.close()
    if image_bytes is None:
        raise HTTPException(status_code=404, detail="no cover art found")
    return Response(
        content=image_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("", response_model=WantedOut)
def create_wanted(payload: WantedCreate, session: Session = Depends(get_session)):
    item = WantedItem(artist=payload.artist, album=payload.album, track=payload.track)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.delete("/{wanted_id}", status_code=204)
def delete_wanted(wanted_id: int, session: Session = Depends(get_session)):
    item = session.get(WantedItem, wanted_id)
    if not item:
        raise HTTPException(status_code=404, detail="wanted item not found")
    session.delete(item)
    session.commit()


@router.post("/{wanted_id}/scan-now", response_model=WantedOut)
def scan_now(wanted_id: int, session: Session = Depends(get_session)):
    item = session.get(WantedItem, wanted_id)
    if not item:
        raise HTTPException(status_code=404, detail="wanted item not found")

    settings = get_settings()
    slskd = SlskdClient.from_settings(settings)
    try:
        process_wanted_item(session, item, slskd, settings)
    finally:
        slskd.close()
    session.refresh(item)
    return item


@router.post("/scan-all")
def scan_all(background_tasks: BackgroundTasks):
    def _run():
        settings = get_settings()
        from sqlmodel import Session as _Session

        from ..database import engine

        slskd = SlskdClient.from_settings(settings)
        try:
            with _Session(engine) as session:
                process_all_wanted(session, slskd, settings)
        finally:
            slskd.close()

    background_tasks.add_task(_run)
    return {"status": "scan started"}

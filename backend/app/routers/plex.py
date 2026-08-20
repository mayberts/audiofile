from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.plex import PlexNotConfigured, get_album_tracks, get_artist_bio, get_library_albums, get_plex_server
from ..config import get_settings
from ..database import get_session
from ..models import LibraryAlbum, WantedItem, WantedSource
from ..schemas import AddMissingAlbumRequest, LibraryAlbumOut, MissingAlbumOut, TrackOut, WantedOut
from ..services.plex_gaps import get_missing_albums_for_artist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plex", tags=["plex"])


@router.get("/library", response_model=list[LibraryAlbumOut])
def get_library(session: Session = Depends(get_session)):
    """Reads the persisted snapshot from the last scan — a fast DB read, no
    Plex call. Returns an empty list if the library has never been scanned."""
    return session.exec(select(LibraryAlbum).order_by(LibraryAlbum.artist, LibraryAlbum.album)).all()


@router.post("/library/scan", response_model=list[LibraryAlbumOut])
def scan_library(session: Session = Depends(get_session)):
    """The only thing that actually talks to Plex for the library listing —
    fetches fresh, replaces the persisted snapshot wholesale, and returns it."""
    settings = get_settings()
    try:
        plex = get_plex_server(settings)
        albums = get_library_albums(plex)
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not load Plex library: {exc}") from exc

    for row in session.exec(select(LibraryAlbum)).all():
        session.delete(row)
    session.commit()

    rows = [LibraryAlbum(**album) for album in albums]
    session.add_all(rows)
    session.commit()
    for row in rows:
        session.refresh(row)
    rows.sort(key=lambda r: (r.artist, r.album))
    return rows


@router.get("/artist/{rating_key}/bio")
def get_artist_bio_endpoint(rating_key: str):
    settings = get_settings()
    try:
        plex = get_plex_server(settings)
        return {"summary": get_artist_bio(plex, rating_key)}
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not load artist bio: {exc}") from exc


@router.get("/artist/{rating_key}/missing-albums", response_model=list[MissingAlbumOut])
def get_missing_albums(rating_key: str):
    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        plex = get_plex_server(settings)
        return get_missing_albums_for_artist(plex, mb, rating_key)
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("Missing-albums check failed for artist %s: %s", rating_key, exc)
        if exc.response.status_code == 503:
            raise HTTPException(
                status_code=503, detail="MusicBrainz is temporarily unavailable — try again in a moment."
            ) from exc
        raise HTTPException(
            status_code=502, detail=f"MusicBrainz returned an error ({exc.response.status_code})."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Missing-albums check failed for artist %s", rating_key)
        raise HTTPException(status_code=502, detail=f"Could not check MusicBrainz: {exc}") from exc
    finally:
        mb.close()


@router.post("/missing-album/add-to-wanted", response_model=WantedOut)
def add_missing_album_to_wanted(payload: AddMissingAlbumRequest, session: Session = Depends(get_session)):
    wanted = WantedItem(
        artist=payload.artist,
        album=payload.album,
        release_group_mbid=payload.release_group_mbid,
        source=WantedSource.PLEX_GAP,
    )
    session.add(wanted)
    session.commit()
    session.refresh(wanted)
    return wanted


@router.get("/album/{rating_key}/tracks", response_model=list[TrackOut])
def get_album_tracks_endpoint(rating_key: str):
    settings = get_settings()
    try:
        plex = get_plex_server(settings)
        return get_album_tracks(plex, rating_key)
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not load tracks: {exc}") from exc


@router.get("/image")
def get_image(path: str):
    """Proxies artwork from Plex so the browser never sees the Plex token —
    library listings only hand out these Plex-relative paths, not full URLs."""
    if not path.startswith("/library/"):
        raise HTTPException(status_code=400, detail="invalid image path")

    settings = get_settings()
    try:
        get_plex_server(settings)  # cheap: reuses the same config validation
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    url = f"{settings.plex_url.rstrip('/')}{path}"
    try:
        resp = httpx.get(url, headers={"X-Plex-Token": settings.plex_token}, timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch image from Plex: {exc}") from exc

    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlmodel import Session, select

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.plex import (
    PlexNotConfigured,
    get_album_tracks,
    get_artist_bio,
    get_item_posters,
    get_library_albums,
    get_plex_server,
    select_item_poster,
    upload_item_poster,
)
from ..config import get_settings
from ..database import get_session
from ..models import LibraryAlbum, WantedItem, WantedSource
from ..schemas import (
    AddMissingAlbumRequest,
    LibraryAlbumOut,
    MissingAlbumOut,
    PosterOut,
    PosterResultOut,
    SelectPosterRequest,
    TrackCheckOut,
    TrackOut,
    WantedOut,
)
from ..services.plex_gaps import get_missing_albums_for_artist, get_missing_tracks_for_album

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
        release_mbid=payload.release_mbid,
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


@router.get("/album/{rating_key}/track-check", response_model=TrackCheckOut)
def get_album_track_check(rating_key: str):
    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        plex = get_plex_server(settings)
        return get_missing_tracks_for_album(plex, mb, rating_key)
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("Track check failed for album %s: %s", rating_key, exc)
        if exc.response.status_code == 503:
            raise HTTPException(
                status_code=503, detail="MusicBrainz is temporarily unavailable — try again in a moment."
            ) from exc
        raise HTTPException(
            status_code=502, detail=f"MusicBrainz returned an error ({exc.response.status_code})."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Track check failed for album %s", rating_key)
        raise HTTPException(status_code=502, detail=f"Could not check MusicBrainz: {exc}") from exc
    finally:
        mb.close()


@router.get("/item/{rating_key}/posters", response_model=list[PosterOut])
def get_item_posters_endpoint(rating_key: str):
    """Works for either an artist or an album ratingKey — Plex treats both
    as plain library items, and posters() returns candidates its music
    metadata agent already found plus anything previously uploaded."""
    settings = get_settings()
    try:
        plex = get_plex_server(settings)
        return get_item_posters(plex, rating_key)
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not load artwork options: {exc}") from exc


@router.post("/item/{rating_key}/poster/select", response_model=PosterResultOut)
def select_item_poster_endpoint(
    rating_key: str, payload: SelectPosterRequest, session: Session = Depends(get_session)
):
    settings = get_settings()
    try:
        plex = get_plex_server(settings)
        thumb = select_item_poster(plex, rating_key, payload.poster_key)
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not set artwork: {exc}") from exc
    _sync_library_thumb(session, rating_key, thumb)
    return {"thumb": thumb}


@router.post("/item/{rating_key}/poster/upload", response_model=PosterResultOut)
async def upload_item_poster_endpoint(
    rating_key: str,
    url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    try:
        plex = get_plex_server(settings)
        file_bytes = await file.read() if file is not None else None
        thumb = upload_item_poster(plex, rating_key, url=url or None, file_bytes=file_bytes)
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not upload artwork: {exc}") from exc
    _sync_library_thumb(session, rating_key, thumb)
    return {"thumb": thumb}


def _sync_library_thumb(session: Session, rating_key: str, thumb: Optional[str]) -> None:
    """Keeps the persisted Library snapshot in step with an artwork change
    (either an artist or an album ratingKey) so it shows up right away
    instead of waiting for the next full 'Scan Plex Library'."""
    if thumb is None:
        return
    for row in session.exec(select(LibraryAlbum).where(LibraryAlbum.rating_key == rating_key)).all():
        row.thumb = thumb
        session.add(row)
    for row in session.exec(select(LibraryAlbum).where(LibraryAlbum.artist_rating_key == rating_key)).all():
        row.artist_thumb = thumb
        session.add(row)
    session.commit()


@router.get("/image")
def get_image(path: str):
    """Proxies artwork from Plex so the browser never sees the Plex token —
    library listings only hand out these Plex-relative paths, not full URLs.
    Posters from posters() can come back as any Plex-internal path (not
    just /library/...), so this only guards against being handed a
    protocol-relative or absolute external URL, not a specific prefix."""
    if not path.startswith("/") or path.startswith("//"):
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

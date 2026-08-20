from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlmodel import Session, select

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.plex import PlexNotConfigured, get_album_tracks, get_library_albums, get_plex_server
from ..config import get_settings
from ..database import get_session
from ..models import PlexMissingAlbum, WantedItem, WantedSource
from ..schemas import LibraryAlbumOut, PlexGapOut, TrackOut
from ..services.plex_gaps import scan_for_gaps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plex", tags=["plex"])


@router.get("/library", response_model=list[LibraryAlbumOut])
def get_library():
    settings = get_settings()
    try:
        plex = get_plex_server(settings)
        return get_library_albums(plex)
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not load Plex library: {exc}") from exc


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


@router.post("/scan")
def trigger_scan(background_tasks: BackgroundTasks, limit_artists: int | None = None):
    settings = get_settings()

    # Fail fast on bad config/connectivity — this check is quick, so it's
    # worth doing synchronously rather than only surfacing it minutes later.
    try:
        get_plex_server(settings)
    except PlexNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not reach Plex: {exc}") from exc

    def _run():
        from sqlmodel import Session as _Session

        from ..database import engine

        mb = MusicBrainzClient(settings)
        try:
            with _Session(engine) as session:
                missing = scan_for_gaps(session, settings, mb, limit_artists=limit_artists)
                logger.info("Plex gap scan finished: %d new missing album(s)", missing)
        except Exception:  # noqa: BLE001
            logger.exception("Plex gap scan failed")
        finally:
            mb.close()

    # The scan itself can take minutes for a real library — MusicBrainz's
    # ~1 req/sec rate limit means two requests per artist adds up fast — so
    # it runs in the background instead of blocking the request past
    # nginx's proxy timeout. Poll GET /api/plex/gaps for results.
    background_tasks.add_task(_run)
    return {"status": "scan started"}


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

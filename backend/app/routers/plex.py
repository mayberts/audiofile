from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.exc import IntegrityError
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
from ..models import LibraryAlbum, WantedItem, WantedSource, compute_wanted_dedup_key
from ..schemas import (
    AddMissingAlbumRequest,
    DismissTrackRequest,
    LibraryAlbumOut,
    MissingAlbumOut,
    PinReleaseRequest,
    PosterOut,
    PosterResultOut,
    SelectPosterRequest,
    TrackCheckOut,
    TrackOut,
    WantedOut,
)
from ..services.plex_gaps import (
    dismiss_track,
    get_dismissed_normalized,
    get_dismissed_titles,
    get_missing_albums_for_artist,
    get_missing_tracks_for_album,
    refresh_album_gap,
    undismiss_track,
    upsert_gap_row,
)

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

    # A rescan replaces every row wholesale -- Plex is the source of truth
    # for everything else here, but a pinned release
    # (LibraryAlbum.pinned_release_mbid/_title, set via "Compare against a
    # different edition") has no Plex-side representation at all, it's
    # purely local to audiofile. Carrying it forward by rating_key (stable
    # across a rescan -- the same assumption _sync_library_thumb already
    # relies on) is the only thing keeping a pin from silently disappearing
    # the next time someone rescans their library.
    pinned_by_rating_key = {
        row.rating_key: (row.pinned_release_mbid, row.pinned_release_title)
        for row in session.exec(select(LibraryAlbum)).all()
        if row.rating_key and row.pinned_release_mbid
    }

    for row in session.exec(select(LibraryAlbum)).all():
        session.delete(row)
    session.commit()

    rows = [LibraryAlbum(**album) for album in albums]
    for row in rows:
        pin = pinned_by_rating_key.get(row.rating_key)
        if pin:
            row.pinned_release_mbid, row.pinned_release_title = pin
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
    # Same insert-first, race-proof dedup as create_wanted (see its
    # comment) -- this endpoint used to have no dedup check at all, so two
    # near-simultaneous "Add" clicks here would always create two
    # independent rows, each running its own full search+download+organize
    # cycle for the same album.
    dedup_key = compute_wanted_dedup_key(payload.artist, payload.album, None)
    wanted = WantedItem(
        artist=payload.artist,
        album=payload.album,
        release_group_mbid=payload.release_group_mbid,
        release_mbid=payload.release_mbid,
        source=WantedSource.PLEX_GAP,
        dedup_key=dedup_key,
    )
    session.add(wanted)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.exec(select(WantedItem).where(WantedItem.dedup_key == dedup_key)).first()
        if existing is None:
            raise
        return existing

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
def get_album_track_check(rating_key: str, release_mbid: str | None = None, session: Session = Depends(get_session)):
    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        plex = get_plex_server(settings)
        dismissed = get_dismissed_normalized(session, rating_key)
        result = get_missing_tracks_for_album(plex, mb, rating_key, release_mbid, dismissed)
        _sync_gap_row_from_check(session, rating_key, result)
        return result
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


@router.post("/album/{rating_key}/release/pin", response_model=LibraryAlbumOut)
def pin_album_release(rating_key: str, payload: PinReleaseRequest, session: Session = Depends(get_session)):
    """Persists a release picked via "Compare against a different edition"
    (AlbumDetailPage) so future visits to this album compare against it
    automatically instead of falling back to search_release()'s guess and
    needing the same release re-found and re-picked every time."""
    row = session.exec(select(LibraryAlbum).where(LibraryAlbum.rating_key == rating_key)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="album not found in library snapshot")
    row.pinned_release_mbid = payload.release_mbid
    row.pinned_release_title = payload.release_title
    session.add(row)
    session.commit()
    session.refresh(row)
    _refresh_gap_for_row(session, row)
    return row


@router.post("/album/{rating_key}/release/unpin", response_model=LibraryAlbumOut)
def unpin_album_release(rating_key: str, session: Session = Depends(get_session)):
    row = session.exec(select(LibraryAlbum).where(LibraryAlbum.rating_key == rating_key)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="album not found in library snapshot")
    row.pinned_release_mbid = None
    row.pinned_release_title = None
    session.add(row)
    session.commit()
    session.refresh(row)
    _refresh_gap_for_row(session, row)
    return row


@router.post("/album/{rating_key}/dismissed-tracks", response_model=list[str])
def dismiss_album_track(rating_key: str, payload: DismissTrackRequest, session: Session = Depends(get_session)):
    """Marks one specific missing-track title as not actually missing (see
    DismissedTrack) -- used from the Missing Tracks page for a track that
    doesn't belong in the count (a bonus track nobody cares about, an
    alternate version MusicBrainz lists separately, a title that just
    doesn't parse right). Immediately refreshes this album's persisted gap
    row so the change is reflected without waiting for the next scan."""
    row = session.exec(select(LibraryAlbum).where(LibraryAlbum.rating_key == rating_key)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="album not found in library snapshot")
    dismiss_track(session, rating_key, payload.title)
    session.commit()
    _refresh_gap_for_row(session, row)
    return get_dismissed_titles(session, rating_key)


@router.delete("/album/{rating_key}/dismissed-tracks", response_model=list[str])
def undismiss_album_track(rating_key: str, title: str, session: Session = Depends(get_session)):
    row = session.exec(select(LibraryAlbum).where(LibraryAlbum.rating_key == rating_key)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="album not found in library snapshot")
    undismiss_track(session, rating_key, title)
    session.commit()
    _refresh_gap_for_row(session, row)
    return get_dismissed_titles(session, rating_key)


def _refresh_gap_for_row(session: Session, row: LibraryAlbum) -> None:
    """Keeps the persisted AlbumTrackGap snapshot (Library page badges,
    Missing Tracks page) in sync with a pin/unpin -- without this, picking
    a different release edition here has no effect on those pages until
    the next full library-wide scan, which on a large library can be a
    long time away. Best-effort: the pin/unpin itself already committed
    successfully by the time this runs, so a Plex/MusicBrainz hiccup here
    shouldn't turn into an error response for what the user actually did."""
    if not row.rating_key:
        return
    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        plex = get_plex_server(settings)
        refresh_album_gap(
            session, plex, mb, row.rating_key, row.artist, row.album, row.thumb, row.pinned_release_mbid
        )
    except Exception:
        logger.exception("could not refresh track-gap row for album %s after pin change", row.rating_key)
    finally:
        mb.close()


def _sync_gap_row_from_check(session: Session, rating_key: str, result: dict) -> None:
    """Keeps the persisted AlbumTrackGap snapshot (Library page badges,
    artist page badges, Missing Tracks page) in sync with *every* live
    track-check here, not just a pin/unpin -- without this, an album a
    full scan once flagged as missing (a wrong auto-matched release, a
    matching-logic fix that landed after that scan ran, whatever) stayed
    flagged everywhere else forever, even after checking it here already
    showed it complete, since nothing else ever re-touched that row short
    of a brand new full-library scan. Reuses the exact same upsert/delete
    logic the full scan and refresh_album_gap use, so all three paths
    can never disagree about what counts as "has a gap." Best-effort:
    this is a side-effect of a read endpoint, so a failure here logs
    rather than turning the live check itself into an error response."""
    row = session.exec(select(LibraryAlbum).where(LibraryAlbum.rating_key == rating_key)).first()
    if row is None:
        return
    try:
        upsert_gap_row(session, rating_key, row.artist, row.album, row.thumb, result)
        session.commit()
    except Exception:
        logger.exception("could not sync track-gap row for album %s after a live check", rating_key)


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

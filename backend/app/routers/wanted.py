from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlmodel import Session, func, select

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.slskd import SlskdClient
from ..config import get_settings
from ..database import get_session
from ..models import DownloadRecord, WantedItem
from ..schemas import MissingAlbumOut, ReleaseEditionOut, WantedCreate, WantedOut
from ..services.plex_gaps import get_artist_discography, get_owned_album_titles_from_snapshot
from ..services.wanted import process_all_wanted, process_wanted_item

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wanted", tags=["wanted"])


@router.get("", response_model=list[WantedOut])
def list_wanted(session: Session = Depends(get_session)):
    return session.exec(select(WantedItem).order_by(WantedItem.created_at.desc())).all()


@router.get("/discography", response_model=list[MissingAlbumOut])
def get_discography(artist: str, session: Session = Depends(get_session)):
    """Backs the album picker shown when adding just an artist name (no
    album/track) — lets someone choose specific albums instead of one
    ambiguous "whole discography" wanted item. Flags albums already in the
    Plex library (from the last library scan) so the picker can show what's
    already owned instead of presenting the full discography as if none of
    it were."""
    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        owned = get_owned_album_titles_from_snapshot(session, artist)
        return get_artist_discography(mb, artist, owned)
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


@router.get("/release-editions", response_model=list[ReleaseEditionOut])
def get_release_editions(release_group_mbid: str):
    """Backs the "choose a specific edition" picker -- every real release
    MusicBrainz has under one release-group, so someone can pick an exact
    pressing (a plain 11-track CD vs. a 26-track deluxe reissue) instead of
    leaving it to a relevance-ranked guess."""
    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        return mb.get_release_group_releases(release_group_mbid)
    except httpx.HTTPStatusError as exc:
        logger.warning("release-editions lookup failed for %r: %s", release_group_mbid, exc)
        if exc.response.status_code == 503:
            raise HTTPException(
                status_code=503, detail="MusicBrainz is temporarily unavailable — try again in a moment."
            ) from exc
        raise HTTPException(
            status_code=502, detail=f"MusicBrainz returned an error ({exc.response.status_code})."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("release-editions lookup failed for %r", release_group_mbid)
        raise HTTPException(status_code=502, detail=f"Could not check MusicBrainz: {exc}") from exc
    finally:
        mb.close()


@router.get("/release-search", response_model=list[ReleaseEditionOut])
def search_releases(artist: str, query: str):
    """Free-text release search, not scoped to any one release-group --
    backs "compare against a different edition" on an owned album, which
    needs to reach releases MusicBrainz models under an entirely different
    title (a bonus-disc reissue like "Album: Side B"), not just other
    pressings of the exact same release-group."""
    settings = get_settings()
    mb = MusicBrainzClient(settings)
    try:
        return mb.search_releases(artist, query)
    except httpx.HTTPStatusError as exc:
        logger.warning("release search failed for %r/%r: %s", artist, query, exc)
        if exc.response.status_code == 503:
            raise HTTPException(
                status_code=503, detail="MusicBrainz is temporarily unavailable — try again in a moment."
            ) from exc
        raise HTTPException(
            status_code=502, detail=f"MusicBrainz returned an error ({exc.response.status_code})."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("release search failed for %r/%r", artist, query)
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


def _norm(value: str | None) -> str | None:
    return value.strip().lower() if value else None


@router.post("", response_model=WantedOut)
def create_wanted(payload: WantedCreate, session: Session = Depends(get_session)):
    # A successfully-downloaded want is deleted (see _sync_wanted_item), so
    # anything still in the table represents an open or retryable want —
    # matching one here means this exact artist/album/track is already
    # tracked, so re-adding it (e.g. clicking "Add" twice, or picking the
    # same album from two different flows) reuses that row instead of
    # kicking off a second, independent search+download for the same thing.
    candidates = session.exec(
        select(WantedItem).where(func.lower(WantedItem.artist) == _norm(payload.artist))
    ).all()
    for existing in candidates:
        if _norm(existing.album) == _norm(payload.album) and _norm(existing.track) == _norm(payload.track):
            # A re-add with a specific edition picked this time (e.g. someone
            # re-adding after removing to switch pressings) should stick --
            # otherwise the picked release_mbid would be silently dropped in
            # favor of whatever this stale row already had (or didn't).
            if payload.release_mbid and existing.release_mbid != payload.release_mbid:
                existing.release_mbid = payload.release_mbid
                session.add(existing)
                session.commit()
                session.refresh(existing)
            return existing

    item = WantedItem(
        artist=payload.artist, album=payload.album, track=payload.track, release_mbid=payload.release_mbid
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.delete("/{wanted_id}", status_code=204)
def delete_wanted(wanted_id: int, session: Session = Depends(get_session)):
    item = session.get(WantedItem, wanted_id)
    if not item:
        raise HTTPException(status_code=404, detail="wanted item not found")

    # Otherwise these become orphaned but still-retryable rows once the
    # wanted item is gone. If the same artist/album is re-added and
    # re-scanned later, clicking Retry on one of these stale leftovers —
    # if its file is still sitting in the download dir from the earlier
    # attempt — tags and organizes it now, landing on the exact same
    # destination path a newer, independent download already organized,
    # producing a silent duplicate on disk.
    records = session.exec(
        select(DownloadRecord).where(DownloadRecord.wanted_item_id == wanted_id)
    ).all()
    for record in records:
        session.delete(record)

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

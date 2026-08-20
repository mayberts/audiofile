from __future__ import annotations

import logging

from sqlmodel import Session, select

from ..clients.slskd import SlskdClient, SlskdError
from ..config import Settings
from ..models import DownloadRecord, DownloadStatus, WantedItem, WantedStatus
from . import search as search_service

logger = logging.getLogger(__name__)


def _build_query(item: WantedItem) -> str:
    if item.track:
        return f"{item.artist} {item.track}"
    return f"{item.artist} {item.album}"


def process_wanted_item(
    session: Session, item: WantedItem, slskd: SlskdClient, settings: Settings
) -> None:
    item.status = WantedStatus.SEARCHING
    session.add(item)
    session.commit()

    try:
        # This runs in the background (scheduler tick or "Scan All Now"), not
        # blocking a page load, so it can afford to wait longer than the
        # interactive search page for slower-to-respond Soulseek peers.
        raw = slskd.search(_build_query(item), timeout_ms=25000)
    except SlskdError as exc:
        logger.warning("search failed for wanted item %s: %s", item.id, exc)
        item.status = WantedStatus.FAILED
        item.last_error = str(exc)
        session.add(item)
        session.commit()
        return

    results = search_service.parse_search_responses(raw)
    match = search_service.best_match(results, settings)

    if match is None:
        item.status = WantedStatus.NOT_FOUND
        item.last_error = "no matching files found on Soulseek"
        session.add(item)
        session.commit()
        return

    try:
        slskd.enqueue_download(match.username, [{"filename": match.filename, "size": match.size}])
    except SlskdError as exc:
        item.status = WantedStatus.FAILED
        item.last_error = str(exc)
        session.add(item)
        session.commit()
        return

    record = DownloadRecord(
        wanted_item_id=item.id,
        slskd_username=match.username,
        slskd_filename=match.filename,
        size_bytes=match.size,
        hint_artist=item.artist,
        hint_album=item.album,
        hint_track=item.track,
        status=DownloadStatus.QUEUED,
    )
    session.add(record)

    item.status = WantedStatus.DOWNLOADING
    item.last_error = None
    session.add(item)
    session.commit()


def process_all_wanted(session: Session, slskd: SlskdClient, settings: Settings) -> int:
    items = session.exec(
        select(WantedItem).where(WantedItem.status == WantedStatus.WANTED)
    ).all()
    for item in items:
        process_wanted_item(session, item, slskd, settings)
    return len(items)

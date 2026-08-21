from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from .clients.musicbrainz import MusicBrainzClient
from .clients.slskd import SlskdClient
from .config import get_settings
from .database import engine
from .models import DownloadRecord, DownloadStatus
from .services import downloads as downloads_service
from .services import wanted as wanted_service

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# Persists for the life of the process, not just one poll tick — a batch
# download large enough to span multiple ticks (e.g. a 22-track 2xCD)
# needs every track resolved against the same MusicBrainz release
# regardless of which tick processes it, or tracks that complete on
# different ticks can land on different editions (different dates) and
# split one album across two differently-named library folders. See the
# comment on resolve_track_metadata for why only successes get cached.
_release_cache: dict = {}


def poll_downloads_job() -> None:
    settings = get_settings()
    slskd = SlskdClient.from_settings(settings)
    mb = MusicBrainzClient(settings)
    try:
        with Session(engine) as session:
            downloads_service.sync_transfer_status(session, slskd)

            completed = session.exec(
                select(DownloadRecord).where(DownloadRecord.status == DownloadStatus.COMPLETED)
            ).all()
            for record in completed:
                downloads_service.process_completed_download(session, record, settings, mb, _release_cache)

            downloads_service.reconcile_stuck_wanted_items(session)
    except Exception:  # noqa: BLE001
        logger.exception("poll_downloads_job failed")
    finally:
        slskd.close()
        mb.close()


def process_wanted_job() -> None:
    settings = get_settings()
    slskd = SlskdClient.from_settings(settings)
    try:
        with Session(engine) as session:
            count = wanted_service.process_all_wanted(session, slskd, settings)
            if count:
                logger.info("processed %s wanted item(s)", count)
    except Exception:  # noqa: BLE001
        logger.exception("process_wanted_job failed")
    finally:
        slskd.close()


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    settings = get_settings()
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        poll_downloads_job,
        "interval",
        seconds=settings.download_poll_interval_seconds,
        id="poll_downloads",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        process_wanted_job,
        "interval",
        minutes=settings.wanted_scan_interval_minutes,
        id="process_wanted",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def reschedule_wanted_scan(minutes: int) -> None:
    """The scheduler only reads Settings once, at startup, to build the
    job's interval trigger — saving a new value via the Settings page
    otherwise has no effect on the already-running job until the process
    restarts. Called after a settings update that touches
    wanted_scan_interval_minutes so it takes effect immediately."""
    if _scheduler is not None:
        _scheduler.reschedule_job("process_wanted", trigger="interval", minutes=minutes)

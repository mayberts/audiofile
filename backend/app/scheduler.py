from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from .clients.musicbrainz import MusicBrainzClient
from .clients.plex import PlexNotConfigured, get_plex_server, refresh_music_library
from .clients.slskd import SlskdClient
from .config import get_settings
from .database import engine
from .models import DownloadRecord, DownloadStatus, TrackGapScan, TrackGapScanStatus
from .services import downloads as downloads_service
from .services import wanted as wanted_service
from .services.plex_gaps import run_track_gap_scan

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
            newly_organized = False
            for record in completed:
                downloads_service.process_completed_download(session, record, settings, mb, _release_cache)
                if record.status == DownloadStatus.DONE:
                    newly_organized = True

            downloads_service.reconcile_stuck_wanted_items(session)

        # Files landing in the library folder don't make Plex aware of them
        # by themselves -- without this, everything in Downloads can show
        # DONE while staying invisible in Plex until its own (possibly
        # infrequent) scan gets to it. A Plex outage/misconfiguration here
        # shouldn't fail the whole poll tick — downloads still need to keep
        # processing even if Plex can't be reached right now.
        if newly_organized:
            try:
                refresh_music_library(get_plex_server(settings))
            except PlexNotConfigured:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("failed to trigger Plex library refresh")
    except Exception:  # noqa: BLE001
        logger.exception("poll_downloads_job failed")
    finally:
        slskd.close()
        mb.close()


def process_wanted_job() -> None:
    settings = get_settings()
    slskd = SlskdClient.from_settings(settings)
    mb = MusicBrainzClient(settings)
    try:
        with Session(engine) as session:
            count = wanted_service.process_all_wanted(session, slskd, settings, mb)
            if count:
                logger.info("processed %s wanted item(s)", count)
    except Exception:  # noqa: BLE001
        logger.exception("process_wanted_job failed")
    finally:
        slskd.close()
        mb.close()


def missing_tracks_scan_job() -> None:
    with Session(engine) as session:
        # A manual scan (or a previous tick, on a library big enough that
        # one scan can outlast the interval) might already be running --
        # skip starting a second, overlapping one rather than racing it.
        current = session.exec(
            select(TrackGapScan).where(TrackGapScan.status == TrackGapScanStatus.RUNNING)
        ).first()
        if current:
            return
        scan = TrackGapScan(status=TrackGapScanStatus.RUNNING)
        session.add(scan)
        session.commit()
        session.refresh(scan)
        scan_id = scan.id
    try:
        run_track_gap_scan(scan_id)
    except Exception:  # noqa: BLE001
        logger.exception("missing_tracks_scan_job failed")


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
    if settings.missing_tracks_scan_interval_minutes > 0:
        scheduler.add_job(
            missing_tracks_scan_job,
            "interval",
            minutes=settings.missing_tracks_scan_interval_minutes,
            id="missing_tracks_scan",
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


def reschedule_missing_tracks_scan(minutes: int) -> None:
    """Same "takes effect immediately" reasoning as reschedule_wanted_scan,
    but this job is also conditionally registered in the first place (0 =
    disabled, see Defaults.missing_tracks_scan_interval_minutes) rather
    than always present at a fixed interval -- so this adds or removes the
    job outright on top of just changing its interval."""
    if _scheduler is None:
        return
    existing = _scheduler.get_job("missing_tracks_scan")
    if minutes <= 0:
        if existing:
            _scheduler.remove_job("missing_tracks_scan")
        return
    if existing:
        _scheduler.reschedule_job("missing_tracks_scan", trigger="interval", minutes=minutes)
    else:
        _scheduler.add_job(
            missing_tracks_scan_job,
            "interval",
            minutes=minutes,
            id="missing_tracks_scan",
            max_instances=1,
            coalesce=True,
        )

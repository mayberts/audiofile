from __future__ import annotations

from datetime import datetime

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from .config import get_settings

_settings = get_settings()
_is_sqlite = _settings.database_url.startswith("sqlite")
# timeout=30: the missing-tracks scan (services/plex_gaps.py) now runs
# several albums' worth of DB commits concurrently from a thread pool --
# without a busy timeout, SQLite raises "database is locked" immediately
# on any write contention instead of briefly waiting for the other writer
# to finish, which a handful of concurrent short-lived commits easily hits.
connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}
engine = create_engine(_settings.database_url, connect_args=connect_args)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_wal_mode(dbapi_connection, _connection_record) -> None:
        # WAL lets readers (e.g. the scan-progress GET endpoint someone's
        # polling in a browser tab) proceed without waiting on whichever
        # thread currently holds the write lock -- default rollback-journal
        # mode blocks readers on writers too, which matters a lot more now
        # that a scan can have several threads committing concurrently.
        dbapi_connection.execute("PRAGMA journal_mode=WAL")


def init_db() -> None:
    from . import models  # noqa: F401  (register models on metadata)

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()
    _recover_interrupted_scans()
    _recover_interrupted_wanted_scans()


def _recover_interrupted_wanted_scans() -> None:
    """A WantedItem only ever reaches SEARCHING from inside
    process_wanted_item (services/wanted.py), which always moves it on to
    a terminal-for-this-attempt outcome (NOT_FOUND/FAILED/AWAITING_REVIEW,
    DOWNLOADING with records, or deletion on success) before returning --
    so a row still SEARCHING at startup means the background task that
    claimed it died mid-scan (a container restart/redeploy), not that a
    scan is genuinely still running (nothing can be, this process just
    started).

    downloads.reconcile_stuck_wanted_items (scheduler.py, runs every poll
    tick) already recovers a stuck DOWNLOADING item once its
    DownloadRecords reach a terminal state, but a SEARCHING item that
    never got that far has no DownloadRecord to reconcile against, so
    that check is permanently a no-op for it. And process_wanted_item's
    own atomic claim explicitly skips any row already SEARCHING/
    DOWNLOADING, so even clicking "Scan" again on a row like this
    silently does nothing -- without this, it would sit there forever."""
    from .models import WantedItem, WantedStatus

    with Session(engine) as session:
        stuck = session.exec(select(WantedItem).where(WantedItem.status == WantedStatus.SEARCHING)).all()
        for item in stuck:
            item.status = WantedStatus.WANTED
            item.last_error = "interrupted by a restart mid-search -- will retry automatically"
            session.add(item)
        if stuck:
            session.commit()


def _recover_interrupted_scans() -> None:
    """A TrackGapScan can only ever reach a terminal status
    (completed/cancelled/failed) from inside run_track_gap_scan
    (services/plex_gaps.py) itself -- so a row still "running" at startup
    means the process died mid-scan (a container restart/redeploy) rather
    than finishing normally. Left alone, that stuck row would block
    POST /api/track-gaps/scan from ever starting a new scan again."""
    from .models import TrackGapScan, TrackGapScanStatus

    with Session(engine) as session:
        stuck = session.exec(
            select(TrackGapScan).where(TrackGapScan.status == TrackGapScanStatus.RUNNING)
        ).all()
        for scan in stuck:
            scan.status = TrackGapScanStatus.FAILED
            scan.last_error = "interrupted by restart"
            scan.finished_at = datetime.utcnow()
            session.add(scan)
        if stuck:
            session.commit()


def _add_missing_columns() -> None:
    """create_all() only creates missing tables — it never alters existing
    ones, so a column added to a model after someone already has a
    populated database needs this instead. No migration framework in this
    project, so just enough ad-hoc ALTER TABLE handling for that case."""
    if not _settings.database_url.startswith("sqlite"):
        return
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(downloadrecord)")}
        if "hint_track_number" not in existing:
            conn.exec_driver_sql("ALTER TABLE downloadrecord ADD COLUMN hint_track_number INTEGER")
            conn.commit()
        if "hint_release_mbid" not in existing:
            conn.exec_driver_sql("ALTER TABLE downloadrecord ADD COLUMN hint_release_mbid VARCHAR")
            conn.commit()
        if "resolved_disc_number" not in existing:
            conn.exec_driver_sql("ALTER TABLE downloadrecord ADD COLUMN resolved_disc_number INTEGER")
            conn.commit()
        if "resolved_track_number" not in existing:
            conn.exec_driver_sql("ALTER TABLE downloadrecord ADD COLUMN resolved_track_number INTEGER")
            conn.commit()

        existing_wanted = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(wanteditem)")}
        if "release_mbid" not in existing_wanted:
            conn.exec_driver_sql("ALTER TABLE wanteditem ADD COLUMN release_mbid VARCHAR")
            conn.commit()
        if "dedup_key" not in existing_wanted:
            conn.exec_driver_sql("ALTER TABLE wanteditem ADD COLUMN dedup_key VARCHAR")
            conn.commit()
            _backfill_wanted_dedup_keys(conn)

        existing_gap = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(albumtrackgap)")}
        if "release_mbid" not in existing_gap:
            conn.exec_driver_sql("ALTER TABLE albumtrackgap ADD COLUMN release_mbid VARCHAR")
            conn.commit()

        existing_library = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(libraryalbum)")}
        if "pinned_release_mbid" not in existing_library:
            conn.exec_driver_sql("ALTER TABLE libraryalbum ADD COLUMN pinned_release_mbid VARCHAR")
            conn.commit()
        if "pinned_release_title" not in existing_library:
            conn.exec_driver_sql("ALTER TABLE libraryalbum ADD COLUMN pinned_release_title VARCHAR")
            conn.commit()

    # A UNIQUE index (not just an index) is what actually makes create_wanted's
    # insert-first dedup race-proof -- a plain index wouldn't stop two
    # concurrent inserts from both succeeding. Created outside the block
    # above so it also gets (re)created for a database that already had
    # dedup_key from a previous partial run but never got this far.
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_wanteditem_dedup_key ON wanteditem (dedup_key)"
        )
        conn.commit()


def _backfill_wanted_dedup_keys(conn) -> None:
    """Computes dedup_key for every existing wanted item, then collapses
    any rows that turn out to already be duplicates (same normalized
    artist/album/track) down to one -- otherwise creating the UNIQUE index
    right after this would fail outright on a database that already has
    duplicate rows from before this dedup existed. Keeps the oldest row in
    each duplicate group and cascades the delete to the removed rows' own
    DownloadRecords (same as the DELETE /api/wanted/{id} endpoint), so a
    stale, retryable record left behind can't get picked up later and
    silently duplicate a file that's already correctly organized."""
    from .models import compute_wanted_dedup_key

    # album/track have been part of this table since it was first created
    # (never themselves added by a migration), so a real deployed database
    # always has them -- but this is a startup migration, and a startup
    # migration that can crash the whole app on some unanticipated schema
    # shape is worse than one that degrades gracefully.
    existing_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(wanteditem)")}
    album_expr = "album" if "album" in existing_cols else "NULL"
    track_expr = "track" if "track" in existing_cols else "NULL"
    rows = conn.exec_driver_sql(f"SELECT id, artist, {album_expr}, {track_expr} FROM wanteditem").fetchall()
    for row_id, artist, album, track in rows:
        key = compute_wanted_dedup_key(artist, album, track)
        conn.exec_driver_sql("UPDATE wanteditem SET dedup_key = ? WHERE id = ?", (key, row_id))
    conn.commit()

    duplicate_groups = conn.exec_driver_sql(
        "SELECT dedup_key, GROUP_CONCAT(id) FROM wanteditem GROUP BY dedup_key HAVING COUNT(*) > 1"
    ).fetchall()
    for _dedup_key, id_list in duplicate_groups:
        ids = sorted(int(i) for i in id_list.split(","))
        keep, remove = ids[0], ids[1:]
        for stale_id in remove:
            conn.exec_driver_sql("DELETE FROM downloadrecord WHERE wanted_item_id = ?", (stale_id,))
            conn.exec_driver_sql("DELETE FROM wanteditem WHERE id = ?", (stale_id,))
    conn.commit()


def get_session():
    with Session(engine) as session:
        yield session

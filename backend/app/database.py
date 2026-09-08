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
    # Order matters: _add_missing_columns adds any columns an existing
    # wanteditem table is still missing (dedup_key in particular) and
    # backfills/deduplicates them on that table before anything else
    # touches it. _ensure_wanted_item_autoincrement then recreates the
    # table fresh -- copying it only after it's already complete means
    # the copy carries real, already-deduplicated data across instead of
    # a freshly-added, still-empty dedup_key column with nothing to copy
    # from the old table (which used to skip the backfill entirely, since
    # by the time _add_missing_columns ran, the recreated table already
    # "had" the column).
    _add_missing_columns()
    _ensure_wanted_item_autoincrement()
    _recover_interrupted_scans()
    _recover_interrupted_wanted_scans()


def _ensure_wanted_item_autoincrement() -> None:
    """One-time migration for a database created before WantedItem declared
    sqlite_autoincrement=True (see models.py for why that matters -- a
    reused id lets a brand new want inherit a deleted one's leftover
    DownloadRecords, which silently deletes it mid-scan). create_all()
    above already gives a fresh install the AUTOINCREMENT table directly
    from the current model, so this only has real work to do on an
    existing database whose wanteditem table predates that.

    SQLite has no ALTER TABLE for adding AUTOINCREMENT to an existing
    table, so this recreates it: rename the old table aside, let
    WantedItem.__table__.create() build the new one (guaranteed to match
    the live model, unlike hand-written DDL), copy every row across by
    whichever columns the old table actually had, then drop the old one.
    Runs after _add_missing_columns (not before) specifically so the copy
    carries real, already-backfilled/deduplicated data -- copying first
    would leave a freshly-added dedup_key column with nothing to copy from
    the old table, and _add_missing_columns would then see the column as
    already present on its next check and skip backfilling it entirely."""
    if not _settings.database_url.startswith("sqlite"):
        return
    from .models import WantedItem

    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='wanteditem'"
        ).fetchone()
        if row is None or (row[0] and "AUTOINCREMENT" in row[0]):
            return  # no table yet (nothing to migrate), or already migrated

        conn.exec_driver_sql("ALTER TABLE wanteditem RENAME TO wanteditem_old")
        WantedItem.__table__.create(conn)

        # A handful of columns are NOT NULL on the model but only get their
        # default applied by the ORM at insert time, never as a real
        # database-level DEFAULT -- so a database old enough to predate
        # this fix can have rows (or, for a genuinely ancient table, not
        # even have the column at all) where a plain column-to-column copy
        # would try to insert NULL and fail outright, taking the whole
        # startup down with it. Every column is included explicitly rather
        # than letting SQLite fall back on its own per-column default: one
        # missing from the old table entirely is substituted with its
        # literal fallback (or NULL, for one that's genuinely optional);
        # one that exists but happens to be NULL on some row is COALESCEd
        # the same way.
        literal_defaults = {
            "status": "'WANTED'",
            "source": "'MANUAL'",
            "created_at": "CURRENT_TIMESTAMP",
            "updated_at": "CURRENT_TIMESTAMP",
        }
        old_columns = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(wanteditem_old)")}
        all_columns = list(WantedItem.__table__.columns.keys())

        def select_expr(c: str) -> str:
            if c not in old_columns:
                # The column didn't exist in the old table at all -- there's
                # nothing to reference, so use the literal fallback (or NULL
                # for a genuinely optional one) unconditionally.
                return literal_defaults.get(c, "NULL")
            if c in literal_defaults:
                return f"COALESCE({c}, {literal_defaults[c]})"
            return c

        dest_list = ", ".join(all_columns)
        select_list = ", ".join(select_expr(c) for c in all_columns)
        conn.exec_driver_sql(f"INSERT INTO wanteditem ({dest_list}) SELECT {select_list} FROM wanteditem_old")
        conn.exec_driver_sql("DROP TABLE wanteditem_old")
        # The dedup_key UNIQUE index lived on the now-dropped old table --
        # recreate it here too rather than relying on WantedItem.__table__
        # .create() alone to have produced an equivalent one, so this
        # can't regress into allowing duplicate wants again just because
        # this migration happened to run.
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_wanteditem_dedup_key ON wanteditem (dedup_key)"
        )
        conn.commit()


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

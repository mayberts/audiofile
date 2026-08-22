from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings

_settings = get_settings()
connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, connect_args=connect_args)


def init_db() -> None:
    from . import models  # noqa: F401  (register models on metadata)

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


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

        existing_wanted = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(wanteditem)")}
        if "release_mbid" not in existing_wanted:
            conn.exec_driver_sql("ALTER TABLE wanteditem ADD COLUMN release_mbid VARCHAR")
            conn.commit()


def get_session():
    with Session(engine) as session:
        yield session

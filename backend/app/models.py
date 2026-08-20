from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class WantedStatus(str, Enum):
    WANTED = "wanted"
    SEARCHING = "searching"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    NOT_FOUND = "not_found"
    FAILED = "failed"


class WantedSource(str, Enum):
    MANUAL = "manual"
    PLEX_GAP = "plex_gap"


class WantedItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    artist: str
    album: Optional[str] = None
    track: Optional[str] = None
    mbid: Optional[str] = None
    release_group_mbid: Optional[str] = None
    status: WantedStatus = Field(default=WantedStatus.WANTED)
    source: WantedSource = Field(default=WantedSource.MANUAL)
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    TAGGING = "tagging"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    wanted_item_id: Optional[int] = Field(default=None, foreign_key="wanteditem.id")

    slskd_username: str
    slskd_filename: str
    size_bytes: Optional[int] = None

    hint_artist: Optional[str] = None
    hint_album: Optional[str] = None
    hint_track: Optional[str] = None
    mbid: Optional[str] = None

    status: DownloadStatus = Field(default=DownloadStatus.QUEUED)
    progress_percent: float = 0.0
    error: Optional[str] = None
    final_path: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

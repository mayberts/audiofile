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


def compute_wanted_dedup_key(artist: str, album: Optional[str], track: Optional[str]) -> str:
    """Shared by WantedItem creation (routers/wanted.py) and the startup
    migration that backfills/deduplicates existing rows (database.py) --
    both need the exact same normalization or the unique index they rely
    on would treat equivalent wants as distinct."""

    def norm(value: Optional[str]) -> str:
        return value.strip().lower() if value else ""

    return f"{norm(artist)}\x1e{norm(album)}\x1e{norm(track)}"


class WantedItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    artist: str
    album: Optional[str] = None
    track: Optional[str] = None
    mbid: Optional[str] = None
    release_group_mbid: Optional[str] = None
    # A specific release (edition/pressing) picked by hand, e.g. via the
    # release-editions picker -- when set, search + tagging resolve against
    # this exact release instead of guessing one from artist/album text.
    release_mbid: Optional[str] = None
    # Normalized "artist|album|track" -- the unique=True below (which
    # create_all() turns into a real UNIQUE constraint for a fresh
    # install; database.py's migration adds the equivalent index by hand
    # for an existing one) is what actually prevents two near-simultaneous
    # "Add" requests for the same want from both seeing "nothing exists
    # yet" and each creating their own row. A read-then-insert check alone
    # doesn't close that window (same class of race as process_wanted_item's
    # scan claim) -- each duplicate row then runs its own fully
    # independent, legitimate search+download+organize cycle, colliding on
    # the same destination files.
    dedup_key: Optional[str] = Field(default=None, unique=True)
    status: WantedStatus = Field(default=WantedStatus.WANTED)
    source: WantedSource = Field(default=WantedSource.MANUAL)
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class LibraryAlbum(SQLModel, table=True):
    """A persisted snapshot of the Plex library, refreshed only when the
    user explicitly rescans — so loading the Library page is a fast DB read
    instead of a live Plex walk on every visit."""

    id: Optional[int] = Field(default=None, primary_key=True)
    artist: str
    artist_thumb: Optional[str] = None
    artist_rating_key: Optional[str] = None
    album: str
    thumb: Optional[str] = None
    year: Optional[int] = None
    track_count: Optional[int] = None
    rating_key: Optional[str] = None


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
    # Set only for album-batch downloads (one wanted item, many files from
    # one folder): a best-effort track position parsed from the remote
    # filename, used to match against the release's actual tracklist since
    # there's no single hint_track title for a whole-folder grab.
    hint_track_number: Optional[int] = None
    # Carried over from the wanted item's own release_mbid (if one was
    # picked by hand) so tagging resolves against that exact release
    # instead of re-guessing one from hint_artist/hint_album text.
    hint_release_mbid: Optional[str] = None
    mbid: Optional[str] = None

    status: DownloadStatus = Field(default=DownloadStatus.QUEUED)
    progress_percent: float = 0.0
    error: Optional[str] = None
    final_path: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

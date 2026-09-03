from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class WantedStatus(str, Enum):
    WANTED = "wanted"
    SEARCHING = "searching"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    # No candidate folder scored confidently enough to trust unattended (see
    # score_album_candidates's "manual" tier) -- pooled candidates are
    # sitting in WantedReviewCandidate waiting for a human pick instead.
    AWAITING_REVIEW = "awaiting_review"


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


class WantedReviewCandidate(SQLModel, table=True):
    """One scored candidate folder pooled for a human to pick from when a
    wanted item's search comes back with nothing confident enough for
    score_album_candidates to auto-pick (see services/wanted.py) -- exists
    only while its WantedItem sits in AWAITING_REVIEW, and is cleared out
    (all rows for that item) as soon as one gets picked or the whole batch
    is rejected."""

    id: Optional[int] = Field(default=None, primary_key=True)
    wanted_item_id: int = Field(foreign_key="wanteditem.id")

    username: str
    directory: str
    file_count: int
    total_size_bytes: int
    score: float
    tier: str

    # JSON-serialized [{filename, size, bitrate, extension}, ...] -- enough
    # to rebuild the enqueue payload if this candidate gets picked, without
    # re-running the search (peer results aren't persisted anywhere else,
    # and re-searching could easily turn up a different folder entirely).
    files_json: str

    created_at: datetime = Field(default_factory=datetime.utcnow)


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
    # A specific release picked by hand via "Compare against a different
    # edition" (the missing-tracks comparison on the album page) -- once
    # set, that comparison always uses this exact release instead of
    # re-guessing one from search_release() on every visit, so a
    # deluxe/bonus-disc reissue picked once doesn't need to be re-found and
    # re-picked again the next time this album's page is opened.
    pinned_release_mbid: Optional[str] = None
    pinned_release_title: Optional[str] = None


class TrackedArtist(SQLModel, table=True):
    """An artist added purely to browse -- shows up on the Library page and
    gets a working artist page (full MusicBrainz discography) even though
    nothing by them exists in the Plex-derived LibraryAlbum snapshot yet.
    Deliberately has no connection to WantedItem/the download pipeline at
    all: tracking an artist here never causes anything to search or
    download -- that only ever happens when something is explicitly added
    to the wanted list. Once real albums for this artist actually appear in
    LibraryAlbum, this row just becomes redundant (the frontend prefers the
    owned entry) rather than needing to be cleaned up."""

    id: Optional[int] = Field(default=None, primary_key=True)
    artist: str = Field(unique=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TrackGapScanStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TrackGapScan(SQLModel, table=True):
    """One run of the full-library missing-tracks scan (services/plex_gaps.py
    run_track_gap_scan). The most recent row (by started_at) is "the"
    current/last scan a client polls -- kept as a normal multi-row table
    (cheap built-in history) rather than trying to enforce a true
    singleton."""

    id: Optional[int] = Field(default=None, primary_key=True)
    status: TrackGapScanStatus = Field(default=TrackGapScanStatus.RUNNING)
    total_albums: int = 0
    checked_albums: int = 0
    # Cooperative cancellation -- the scan loop checks this once per album
    # rather than being killed outright, so it never leaves a half-written
    # AlbumTrackGap row or an inconsistent checked_albums count behind.
    cancel_requested: bool = False
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    last_error: Optional[str] = None


class AlbumTrackGap(SQLModel, table=True):
    """One row per album the most recent scan found missing tracks for --
    only albums with real gaps are stored here (nothing for a complete
    album), upserted or deleted per album as the scan progresses so results
    are visible while the scan is still running and an album fixed since
    the last scan disappears on the next one without a full wipe first."""

    id: Optional[int] = Field(default=None, primary_key=True)
    rating_key: str = Field(unique=True)
    artist: str
    album: str
    thumb: Optional[str] = None
    expected_total: Optional[int] = None
    owned_total: int
    missing_count: int
    missing_tracks_json: str
    release_mbid: Optional[str] = None
    release_title: Optional[str] = None
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class DismissedTrack(SQLModel, table=True):
    """A specific track title someone has marked "not actually missing" for
    one album -- a bonus track they don't care about, an alternate version
    MusicBrainz lists separately, or a title that just doesn't parse
    cleanly. Excluded from get_missing_tracks_for_album's missing list (and
    therefore from both the live AlbumDetailPage check and the persisted
    AlbumTrackGap snapshot) for that rating_key going forward, without
    pretending the track is actually owned."""

    id: Optional[int] = Field(default=None, primary_key=True)
    rating_key: str = Field(index=True)
    title: str
    normalized_title: str = Field(index=True)
    dismissed_at: datetime = Field(default_factory=datetime.utcnow)

    __table_args__ = (UniqueConstraint("rating_key", "normalized_title", name="ix_dismissedtrack_rating_key_title"),)


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
    # The (disc, track) position this file actually resolved to against a
    # release's own canonical tracklist -- set only once resolve_track_metadata
    # found a real match, not from an unverified filename guess. Lets a later
    # sibling download that resolves to the same slot be recognized as a
    # duplicate at import time (see process_completed_download), regardless
    # of whether its filename looked anything like the one that got there
    # first.
    resolved_disc_number: Optional[int] = None
    resolved_track_number: Optional[int] = None

    status: DownloadStatus = Field(default=DownloadStatus.QUEUED)
    progress_percent: float = 0.0
    error: Optional[str] = None
    final_path: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

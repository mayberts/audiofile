from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from .models import DownloadStatus, TrackGapScanStatus, WantedSource, WantedStatus


class SearchRequest(BaseModel):
    query: str
    # This endpoint runs synchronously inside the request (unlike
    # services/wanted.py's searches, which all run as background tasks and
    # can afford to wait much longer -- see _FIRST_RUNG_TIMEOUT_MS), so its
    # patience is deliberately capped well under nginx's proxy_read_timeout
    # (160s -- see frontend/nginx.conf) rather than matching that value:
    # raising this without also raising nginx's timeout would just trade
    # "gives up too early" for "the connection gets killed before the
    # backend even finishes." 120s already fixed the original problem this
    # was tuned for (a 20s default that gave up long before slskd's own UI,
    # which has no deadline, would still be accumulating real matches) --
    # if a search needs more patience than that, add it to the wanted list
    # instead, where it isn't bound by a live connection at all.
    timeout_ms: int = 120000


class SearchFile(BaseModel):
    username: str
    filename: str
    size: int
    bitrate: Optional[int] = None
    length_seconds: Optional[int] = None
    extension: str
    slots_free: bool
    upload_speed: Optional[int] = None
    queue_length: Optional[int] = None
    score: float = 0.0


class SearchResponse(BaseModel):
    query: str
    results: list[SearchFile]


class DownloadRequest(BaseModel):
    username: str
    filename: str
    size: int
    hint_artist: Optional[str] = None
    hint_album: Optional[str] = None
    hint_track: Optional[str] = None
    mbid: Optional[str] = None
    wanted_item_id: Optional[int] = None


class DownloadOut(BaseModel):
    id: int
    slskd_username: str
    slskd_filename: str
    status: DownloadStatus
    progress_percent: float
    error: Optional[str] = None
    final_path: Optional[str] = None
    hint_artist: Optional[str] = None
    hint_album: Optional[str] = None
    hint_track: Optional[str] = None

    class Config:
        from_attributes = True


class WantedCreate(BaseModel):
    artist: str
    album: Optional[str] = None
    track: Optional[str] = None
    release_mbid: Optional[str] = None


class WantedOut(BaseModel):
    id: int
    artist: str
    album: Optional[str] = None
    track: Optional[str] = None
    release_mbid: Optional[str] = None
    status: WantedStatus
    source: WantedSource
    last_error: Optional[str] = None

    class Config:
        from_attributes = True


class WantedReviewCandidateOut(BaseModel):
    id: int
    username: str
    directory: str
    file_count: int
    total_size_bytes: int
    score: float
    tier: str

    class Config:
        from_attributes = True


class ReleaseEditionOut(BaseModel):
    release_mbid: str
    title: str
    disambiguation: Optional[str] = None
    date: Optional[str] = None
    country: Optional[str] = None
    track_count: int
    format: Optional[str] = None
    label: Optional[str] = None
    catalog_number: Optional[str] = None
    barcode: Optional[str] = None
    status: Optional[str] = None


class LibraryAlbumOut(BaseModel):
    artist: str
    artist_thumb: Optional[str] = None
    artist_rating_key: Optional[str] = None
    album: str
    thumb: Optional[str] = None
    year: Optional[int] = None
    track_count: Optional[int] = None
    rating_key: Optional[str] = None
    pinned_release_mbid: Optional[str] = None
    pinned_release_title: Optional[str] = None

    class Config:
        from_attributes = True


class TrackOut(BaseModel):
    title: str
    track_number: Optional[int] = None
    duration_ms: Optional[int] = None


class MissingAlbumOut(BaseModel):
    album: str
    release_group_mbid: Optional[str] = None
    first_release_date: Optional[str] = None
    in_library: bool = False


class TrackedArtistOut(BaseModel):
    artist: str

    class Config:
        from_attributes = True


class TrackArtistRequest(BaseModel):
    artist: str


class AddMissingAlbumRequest(BaseModel):
    artist: str
    album: str
    release_group_mbid: Optional[str] = None
    release_mbid: Optional[str] = None


class MissingTrackOut(BaseModel):
    title: str
    track_number: Optional[int] = None
    disc: Optional[int] = None


class PosterOut(BaseModel):
    key: str
    thumb: Optional[str] = None
    provider: Optional[str] = None
    selected: bool = False


class SelectPosterRequest(BaseModel):
    poster_key: str


class PosterResultOut(BaseModel):
    thumb: Optional[str] = None


class PinReleaseRequest(BaseModel):
    release_mbid: str
    release_title: str


class DismissTrackRequest(BaseModel):
    title: str


class TrackCheckOut(BaseModel):
    checked: bool
    expected_total: Optional[int] = None
    owned_total: int
    missing_tracks: list[MissingTrackOut] = []
    release_mbid: Optional[str] = None
    release_title: Optional[str] = None


class TrackGapScanOut(BaseModel):
    id: int
    status: TrackGapScanStatus
    total_albums: int
    checked_albums: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    last_error: Optional[str] = None

    class Config:
        from_attributes = True


class AlbumTrackGapOut(BaseModel):
    rating_key: str
    artist: str
    album: str
    thumb: Optional[str] = None
    expected_total: Optional[int] = None
    owned_total: int
    missing_count: int
    missing_tracks: list[str] = []
    release_mbid: Optional[str] = None
    release_title: Optional[str] = None
    checked_at: datetime

    class Config:
        from_attributes = True


class TestSlskdRequest(BaseModel):
    url: str
    api_key: str = ""


class TestPlexRequest(BaseModel):
    url: str
    token: str = ""


class ConnectionTestResult(BaseModel):
    connected: bool
    detail: Optional[str] = None


class SettingsUpdate(BaseModel):
    slskd_url: Optional[str] = None
    slskd_api_key: Optional[str] = None
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
    musicbrainz_contact: Optional[str] = None
    musicbrainz_base_url: Optional[str] = None
    musicbrainz_rate_limit_per_sec: Optional[int] = None
    musicbrainz_concurrent_requests: Optional[int] = None
    download_dir: Optional[str] = None
    library_dir: Optional[str] = None
    wanted_scan_interval_minutes: Optional[int] = None
    preferred_formats: Optional[str] = None
    min_bitrate_kbps: Optional[int] = None

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .models import DownloadStatus, WantedSource, WantedStatus


class SearchRequest(BaseModel):
    query: str
    timeout_ms: int = 6000


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


class WantedOut(BaseModel):
    id: int
    artist: str
    album: Optional[str] = None
    track: Optional[str] = None
    status: WantedStatus
    source: WantedSource
    last_error: Optional[str] = None

    class Config:
        from_attributes = True


class LibraryAlbumOut(BaseModel):
    artist: str
    artist_thumb: Optional[str] = None
    album: str
    year: Optional[int] = None
    track_count: Optional[int] = None


class PlexGapOut(BaseModel):
    id: int
    artist: str
    album: str
    release_group_mbid: Optional[str] = None
    first_release_date: Optional[str] = None
    added_to_wanted: bool

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
    download_dir: Optional[str] = None
    library_dir: Optional[str] = None
    wanted_scan_interval_minutes: Optional[int] = None
    preferred_formats: Optional[str] = None
    min_bitrate_kbps: Optional[int] = None

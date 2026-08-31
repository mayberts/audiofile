from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_OVERRIDE_FILE = DATA_DIR / "settings.json"


class Defaults(BaseSettings):
    """Values that come from the environment / .env at process start."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    slskd_url: str = "http://localhost:5030"
    slskd_api_key: str = ""

    plex_url: str = ""
    plex_token: str = ""

    musicbrainz_contact: str = "audiofile@example.com"
    # Points at the public API by default -- overridable for a self-hosted
    # MusicBrainz mirror (e.g. https://mb.example.internal/ws/2). No
    # trailing slash expected, matching how the public API's own path is
    # written everywhere else in clients/musicbrainz.py.
    musicbrainz_base_url: str = "https://musicbrainz.org/ws/2"
    # The public API's own documented limit -- safe to raise a lot higher
    # for a self-hosted mirror, which has no such constraint. 0 = no
    # client-side pacing at all.
    musicbrainz_rate_limit_per_sec: int = 1
    # MusicBrainz's own "official default" for concurrent in-flight
    # requests from one client, per their API guidance -- also doubles as
    # the worker count for the library-wide missing-tracks scan
    # (services/plex_gaps.py run_track_gap_scan), so raising this actually
    # speeds up a full-library scan against a fast mirror, not just removes
    # the artificial per-request delay.
    musicbrainz_concurrent_requests: int = 6

    download_dir: str = "/downloads"
    library_dir: str = "/music"

    database_url: str = f"sqlite:///{DATA_DIR / 'audiofile.db'}"

    wanted_scan_interval_minutes: int = 30
    download_poll_interval_seconds: int = 15

    preferred_formats: str = "flac,mp3"
    min_bitrate_kbps: int = 192


class Settings(BaseModel):
    """Runtime settings: env defaults overlaid with anything saved via the UI."""

    slskd_url: str
    slskd_api_key: str
    plex_url: str
    plex_token: str
    musicbrainz_contact: str
    musicbrainz_base_url: str
    musicbrainz_rate_limit_per_sec: int
    musicbrainz_concurrent_requests: int
    download_dir: str
    library_dir: str
    database_url: str
    wanted_scan_interval_minutes: int
    download_poll_interval_seconds: int
    preferred_formats: str
    min_bitrate_kbps: int

    @property
    def preferred_format_list(self) -> list[str]:
        return [f.strip().lower() for f in self.preferred_formats.split(",") if f.strip()]

    def masked(self) -> dict:
        data = self.model_dump()
        for secret_key in ("slskd_api_key", "plex_token"):
            if data.get(secret_key):
                data[secret_key] = "•" * 8
        return data


def _load_overrides() -> dict:
    if SETTINGS_OVERRIDE_FILE.exists():
        try:
            return json.loads(SETTINGS_OVERRIDE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def get_settings() -> Settings:
    defaults = Defaults().model_dump()
    overrides = _load_overrides()
    defaults.update({k: v for k, v in overrides.items() if v not in (None, "")})
    return Settings(**defaults)


def update_settings(patch: dict) -> Settings:
    current = _load_overrides()
    for key, value in patch.items():
        if value is not None and value != "":
            current[key] = value
    SETTINGS_OVERRIDE_FILE.write_text(json.dumps(current, indent=2))
    return get_settings()

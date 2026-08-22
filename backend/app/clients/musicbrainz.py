from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from ..config import Settings

MB_BASE = "https://musicbrainz.org/ws/2"
COVER_ART_BASE = "https://coverartarchive.org"

# MusicBrainz's search API parses queries as Lucene syntax, so names
# containing Lucene operators/quote characters (AC/DC, "Weird Al" Yankovic,
# Panic! at the Disco, blink-182, Wu-Tang Clan, ...) need those characters
# escaped or the query is malformed and MusicBrainz returns a 400.
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/&|])')


def _escape_lucene(text: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", text)

# MusicBrainz asks for at most ~1 request/second from a given client.
_last_request_lock = threading.Lock()
_last_request_time = 0.0
_MIN_INTERVAL_S = 1.05


def _throttle() -> None:
    global _last_request_time
    with _last_request_lock:
        wait = _last_request_time + _MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.monotonic()


@dataclass
class TrackMetadata:
    artist: str
    album: str
    title: str
    track_number: Optional[int] = None
    total_tracks: Optional[int] = None
    disc_number: Optional[int] = None
    year: Optional[str] = None
    genre: Optional[str] = None
    mbid: Optional[str] = None
    release_mbid: Optional[str] = None
    release_group_mbid: Optional[str] = None


# MusicBrainz's release search ranks purely by text relevance, with no
# regard for what kind of release something is -- a single sharing its
# parent album's exact title ("Sweat" the song vs "Sweat" the album) can
# easily outrank the album itself. Picking that result silently compares a
# library against a 1-track release instead of the real tracklist, which
# always reports "nothing missing" no matter how much of the album is
# actually absent. A release-group's primary type ("Album" vs "Single"/"EP"/
# etc.) and its track count are both included right in the search response,
# so the true album can be preferred over a same-titled single without an
# extra lookup per candidate.
_PREFERRED_PRIMARY_TYPES = {"Album", "EP"}


def _release_track_count(release: dict) -> int:
    media = release.get("media") or []
    return release.get("track-count") or sum(m.get("track-count") or 0 for m in media)


def _release_rank(release: dict) -> tuple[bool, int]:
    release_group = release.get("release-group") or {}
    is_preferred_type = (
        release_group.get("primary-type") in _PREFERRED_PRIMARY_TYPES
        and not release_group.get("secondary-types")
    )
    # Among same-type candidates, the fullest edition (e.g. a deluxe
    # reissue with bonus tracks) is the most useful one to compare a
    # library against -- it can only ever flag more of what's really
    # missing, never less.
    return (is_preferred_type, _release_track_count(release))


@dataclass
class ReleaseMatch:
    release_mbid: str
    release_group_mbid: str
    artist: str
    title: str
    date: Optional[str]
    tracks: list[dict] = field(default_factory=list)


class MusicBrainzClient:
    def __init__(self, settings: Settings):
        user_agent = f"audiofile/0.1.0 ( {settings.musicbrainz_contact} )"
        self._client = httpx.Client(
            base_url=MB_BASE,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=15.0,
        )
        self._cover_client = httpx.Client(
            base_url=COVER_ART_BASE,
            headers={"User-Agent": user_agent},
            timeout=15.0,
            # Cover Art Archive's /front convenience endpoints respond with a
            # redirect to the actual image (hosted on archive.org), not the
            # image bytes at that URL directly — httpx doesn't follow
            # redirects by default, so without this every cover-art fetch
            # silently got a 3xx back and returned None.
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()
        self._cover_client.close()

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "fmt": "json"}
        attempts = 3
        for attempt in range(attempts):
            _throttle()
            resp = self._client.get(path, params=params)
            # MusicBrainz's own guidance is that clients should back off and
            # retry on 503 — it's load-shedding, not a real client error.
            if resp.status_code == 503 and attempt < attempts - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        raise AssertionError("unreachable")  # loop always returns or raises above

    def search_release(self, artist: str, album: str) -> Optional[ReleaseMatch]:
        query = f'artist:"{_escape_lucene(artist)}" AND release:"{_escape_lucene(album)}"'
        data = self._get("/release", {"query": query, "limit": 10})
        releases = data.get("releases", [])
        if not releases:
            return None
        best = max(releases, key=_release_rank)
        return self._to_release_match(best)

    def get_release(self, release_mbid: str) -> Optional[ReleaseMatch]:
        data = self._get(
            f"/release/{release_mbid}",
            {"inc": "recordings+artist-credits+release-groups"},
        )
        return self._to_release_match(data)

    def _to_release_match(self, data: dict) -> ReleaseMatch:
        artist_credit = data.get("artist-credit", [])
        artist_name = "".join(
            (c.get("name", "") + c.get("joinphrase", "")) if isinstance(c, dict) else str(c)
            for c in artist_credit
        ) or data.get("artist-credit-phrase", "")

        release_group = data.get("release-group", {}) or {}
        tracks: list[dict] = []
        for medium in data.get("media", []):
            disc_number = medium.get("position", 1)
            for track in medium.get("tracks", []):
                tracks.append(
                    {
                        "title": track.get("title"),
                        "position": track.get("position"),
                        "disc": disc_number,
                        "length_ms": track.get("length"),
                        "recording_mbid": (track.get("recording") or {}).get("id"),
                    }
                )

        return ReleaseMatch(
            release_mbid=data["id"],
            release_group_mbid=release_group.get("id", ""),
            artist=artist_name,
            title=data.get("title", ""),
            date=data.get("date") or release_group.get("first-release-date"),
            tracks=tracks,
        )

    def search_recording(self, artist: str, track: str) -> Optional[TrackMetadata]:
        query = f'artist:"{_escape_lucene(artist)}" AND recording:"{_escape_lucene(track)}"'
        data = self._get("/recording", {"query": query, "limit": 5})
        recordings = data.get("recordings", [])
        if not recordings:
            return None
        rec = recordings[0]
        artist_credit = rec.get("artist-credit", [])
        artist_name = "".join(
            (c.get("name", "") + c.get("joinphrase", "")) if isinstance(c, dict) else str(c)
            for c in artist_credit
        ) or rec.get("artist-credit-phrase", artist)

        release = (rec.get("releases") or [{}])[0]
        return TrackMetadata(
            artist=artist_name,
            album=release.get("title", ""),
            title=rec.get("title", track),
            year=(release.get("date") or "")[:4] or None,
            mbid=rec.get("id"),
            release_mbid=release.get("id"),
        )

    def get_artist_release_groups(self, artist_mbid: str) -> list[dict]:
        # `status` only filters inc=releases — release-groups have no status
        # field (that's a per-release property) — so it can't be passed here;
        # doing so makes MusicBrainz reject the whole request with a 400.
        data = self._get(
            f"/artist/{artist_mbid}",
            {"inc": "release-groups", "type": "album"},
        )
        return data.get("release-groups", [])

    def search_artist(self, name: str) -> Optional[dict]:
        data = self._get("/artist", {"query": f'artist:"{_escape_lucene(name)}"', "limit": 5})
        artists = data.get("artists", [])
        return artists[0] if artists else None

    def get_cover_art(self, release_mbid: str) -> Optional[bytes]:
        for kind, mbid in (("release", release_mbid),):
            try:
                _throttle()
                resp = self._cover_client.get(f"/{kind}/{mbid}/front")
                if resp.status_code == 200:
                    return resp.content
            except httpx.HTTPError:
                continue
        return None

    def get_release_group_cover_art(self, release_group_mbid: str) -> Optional[bytes]:
        try:
            _throttle()
            resp = self._cover_client.get(f"/release-group/{release_group_mbid}/front")
            if resp.status_code == 200:
                return resp.content
        except httpx.HTTPError:
            pass
        return None

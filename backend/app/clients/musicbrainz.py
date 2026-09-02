from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from ..config import Settings, get_settings

COVER_ART_BASE = "https://coverartarchive.org"

# MusicBrainz's search API parses queries as Lucene syntax, so names
# containing Lucene operators/quote characters (AC/DC, "Weird Al" Yankovic,
# Panic! at the Disco, blink-182, Wu-Tang Clan, ...) need those characters
# escaped or the query is malformed and MusicBrainz returns a 400.
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/&|])')


def _escape_lucene(text: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", text)


# Pacing (requests/sec) and concurrency (max in-flight requests) are both
# process-wide, not per-MusicBrainzClient-instance -- every call site
# (tagging one track, checking one album, a library-wide scan running many
# checks in parallel) creates its own short-lived MusicBrainzClient, so a
# per-instance limiter would let several instances each independently pace
# themselves and blow straight through the real total request rate/
# concurrency the configured server should see. Settings.
# musicbrainz_rate_limit_per_sec/_concurrent_requests are read fresh on
# every call (cheap -- local env + a small JSON file, no network) rather
# than captured once, so a change on the Settings page takes effect
# immediately, including on a scan already in progress.
_last_request_lock = threading.Lock()
_last_request_time = 0.0

_concurrency_lock = threading.Lock()
_concurrency_semaphore: threading.Semaphore | None = None
_concurrency_semaphore_limit: int | None = None


def _throttle() -> None:
    global _last_request_time
    rate = get_settings().musicbrainz_rate_limit_per_sec
    if rate <= 0:
        return
    min_interval = 1.0 / rate
    with _last_request_lock:
        wait = _last_request_time + min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.monotonic()


def _concurrency_slot() -> threading.Semaphore:
    """Returns the current shared semaphore, rebuilding it if the
    configured limit has changed since the last call. A caller that
    acquires a specific semaphore object and later releases that same
    object stays correct even if this rebuilds a new one for other callers
    in between -- only the *count* of permits changes going forward, never
    an in-flight caller's own acquire/release pairing."""
    global _concurrency_semaphore, _concurrency_semaphore_limit
    limit = max(1, get_settings().musicbrainz_concurrent_requests)
    with _concurrency_lock:
        if _concurrency_semaphore is None or _concurrency_semaphore_limit != limit:
            _concurrency_semaphore = threading.Semaphore(limit)
            _concurrency_semaphore_limit = limit
        return _concurrency_semaphore


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
    # MusicBrainz's own disambiguation for the release/release-group, e.g.
    # "Teal Album" or "Black Album" for Weezer's several self-titled
    # releases. See organizer.library_path_for -- without this, two
    # distinct albums that happen to share both a title and a release year
    # (both true for those two Weezer records, both 2019) compute the exact
    # same destination folder and get physically merged on disk.
    album_disambiguation: Optional[str] = None


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
#
# Only the *primary* type is used for this -- secondary types (Live,
# Compilation, Remix, ...) intentionally aren't disqualifying. An owned
# album can itself be a live album or a compilation, in which case its own
# correct release-group always carries one of those secondary types, so
# requiring their absence made the *correct* match permanently rank below
# literally any other same-titled hit that happened to have none (even a
# single-track one) -- e.g. a live album would reliably match some
# unrelated release instead of its own, comparing the library against a
# completely different tracklist and reporting nearly everything missing.
_PREFERRED_PRIMARY_TYPES = {"Album", "EP"}


def _release_track_count(release: dict) -> int:
    media = release.get("media") or []
    return release.get("track-count") or sum(m.get("track-count") or 0 for m in media)


def _release_rank(release: dict) -> tuple[bool, int]:
    release_group = release.get("release-group") or {}
    is_preferred_type = release_group.get("primary-type") in _PREFERRED_PRIMARY_TYPES
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
    disambiguation: Optional[str] = None


class MusicBrainzClient:
    def __init__(self, settings: Settings):
        user_agent = f"audiofile/0.1.0 ( {settings.musicbrainz_contact} )"
        self._client = httpx.Client(
            base_url=settings.musicbrainz_base_url,
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
        # Held for this whole logical fetch (including its own 503
        # retries), not re-acquired per HTTP attempt -- "at most N of these
        # in flight at once" is the actual guarantee wanted, and letting a
        # retry sneak back in past a full semaphore would just mean more
        # than N real requests reaching the server simultaneously.
        slot = _concurrency_slot()
        slot.acquire()
        try:
            for attempt in range(attempts):
                _throttle()
                resp = self._client.get(path, params=params)
                # MusicBrainz's own guidance is that clients should back off
                # and retry on 503 — it's load-shedding, not a real client
                # error.
                if resp.status_code == 503 and attempt < attempts - 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.json()
            raise AssertionError("unreachable")  # loop always returns or raises above
        finally:
            slot.release()

    def search_release(self, artist: str, album: str) -> Optional[ReleaseMatch]:
        query = f'artist:"{_escape_lucene(artist)}" AND release:"{_escape_lucene(album)}"'
        data = self._get("/release", {"query": query, "limit": 10})
        releases = data.get("releases", [])
        if not releases:
            return None
        best = max(releases, key=_release_rank)
        return self._to_release_match(best)

    def search_release_candidates(self, artist: str, album: str, limit: int = 10) -> list[dict]:
        """Raw release search hits for a track-by-track comparison caller
        (see plex_gaps.get_missing_tracks_for_album) that needs to actually
        try several editions rather than commit to search_release()'s
        single relevance-ranked guess -- that guess prefers the fullest
        (most tracks) edition among preferred types, which for someone who
        owns a plain/standard pressing means comparing against a deluxe
        reissue's bonus tracks they never had, and reporting them as
        "missing" tracks that were never really missing.

        Sorted smallest-track-count first: the caller fetches each
        candidate's full tracklist in this order and stops at the first
        one that already accounts for every track the library has, which
        -- since it's the smallest such candidate -- is the edition least
        likely to blame the library for tracks that belong to some bigger
        reissue."""
        query = f'artist:"{_escape_lucene(artist)}" AND release:"{_escape_lucene(album)}"'
        data = self._get("/release", {"query": query, "limit": limit})
        releases = data.get("releases", [])
        if not releases:
            return []
        preferred = [
            r for r in releases if (r.get("release-group") or {}).get("primary-type") in _PREFERRED_PRIMARY_TYPES
        ]
        candidates = preferred or releases
        candidates.sort(key=_release_track_count)
        return candidates

    def get_release(self, release_mbid: str) -> Optional[ReleaseMatch]:
        data = self._get(
            f"/release/{release_mbid}",
            {"inc": "recordings+artist-credits+release-groups"},
        )
        return self._to_release_match(data)

    def search_releases(self, artist: str, query: str, limit: int = 15) -> list[dict]:
        """Free-text release search returning several candidate summaries,
        not just the single top relevance match search_release() commits
        to -- used to let someone browse for and pin a release that isn't
        even the one search_release() would guess (a differently-titled
        deluxe/bonus-disc reissue like "Album: Side B", which MusicBrainz
        often models as its own separate release rather than another
        edition of the same release-group)."""
        lucene_query = f'artist:"{_escape_lucene(artist)}" AND release:"{_escape_lucene(query)}"'
        data = self._get("/release", {"query": lucene_query, "limit": limit})
        return [self._to_release_summary(r) for r in data.get("releases", [])]

    def get_release_group_releases(self, release_group_mbid: str) -> list[dict]:
        """Every specific release (edition/pressing) MusicBrainz has under one
        release-group -- an album can easily have a dozen (different country
        pressings, a plain CD vs. a deluxe reissue with bonus tracks), and
        search_release()'s relevance-ranked guess at "the" release is never
        going to suit everyone. Lets a caller show the real options and let
        someone pick a specific one instead."""
        data = self._get(
            "/release",
            {"release-group": release_group_mbid, "inc": "media+labels", "limit": 100},
        )
        releases = data.get("releases", [])
        summaries = [self._to_release_summary(r) for r in releases]
        # Bootlegs/promos sharing the release-group are rarely what someone
        # means to pick, but only hidden when at least one official release
        # actually exists -- an release-group with nothing but a bootleg
        # listed is still better shown than shown empty.
        official = [s for s in summaries if s["status"] == "Official"]
        return official or summaries

    def _to_release_summary(self, data: dict) -> dict:
        media = data.get("media") or []
        track_count = sum(m.get("track-count") or 0 for m in media)

        format_counts: dict[str, int] = {}
        for m in media:
            fmt = m.get("format")
            if fmt:
                format_counts[fmt] = format_counts.get(fmt, 0) + 1
        format_str = " + ".join(
            f"{n}×{fmt}" if n > 1 else fmt for fmt, n in format_counts.items()
        ) or None

        release_events = data.get("release-events") or []
        first_event_area = (release_events[0].get("area") or {}) if release_events else {}
        country = data.get("country") or (first_event_area.get("iso-3166-1-codes") or [None])[0]
        date = data.get("date") or (release_events[0].get("date") if release_events else None)

        label_info = data.get("label-info") or []
        label = ((label_info[0].get("label") or {}).get("name") if label_info else None)
        catalog_number = label_info[0].get("catalog-number") if label_info else None

        return {
            "release_mbid": data["id"],
            "title": data.get("title", ""),
            "disambiguation": data.get("disambiguation") or None,
            "date": date,
            "country": country,
            "track_count": track_count,
            "format": format_str,
            "label": label,
            "catalog_number": catalog_number,
            "barcode": data.get("barcode"),
            "status": data.get("status"),
        }

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
            # The release-group's own disambiguation ("Teal Album") is
            # preferred over the specific release's (often blank, or a
            # pressing-level note like "US CD") -- it's the whole album's
            # identity that needs distinguishing from a same-titled sibling,
            # not this particular edition of it.
            disambiguation=release_group.get("disambiguation") or data.get("disambiguation") or None,
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

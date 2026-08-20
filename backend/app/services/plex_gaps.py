from __future__ import annotations

import logging

from sqlmodel import Session, select

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.plex import get_artist_album_titles, iter_music_artists, get_plex_server
from ..config import Settings
from ..models import PlexMissingAlbum

logger = logging.getLogger(__name__)

# Secondary/live/compilation-style release groups are rarely what someone
# means by "fill the gaps in my library", so keep the default scan to
# primary studio albums only.
SKIP_SECONDARY_TYPES = {"Live", "Compilation", "Remix", "DJ-mix", "Mixtape/Street", "Demo"}


def _is_studio_album(rg: dict) -> bool:
    secondary = set(rg.get("secondary-types", []))
    return not (secondary & SKIP_SECONDARY_TYPES)


def scan_for_gaps(session: Session, settings: Settings, mb: MusicBrainzClient, limit_artists: int | None = None) -> int:
    plex = get_plex_server(settings)
    missing_count = 0
    scanned = 0

    for artist in iter_music_artists(plex):
        if limit_artists and scanned >= limit_artists:
            break
        scanned += 1

        try:
            mb_artist = mb.search_artist(artist.title)
            if not mb_artist:
                continue

            owned = get_artist_album_titles(artist)
            release_groups = mb.get_artist_release_groups(mb_artist["id"])
        except Exception:  # noqa: BLE001 — one bad artist shouldn't abort the whole scan
            logger.warning("skipping %r during Plex gap scan", artist.title, exc_info=True)
            continue

        for rg in release_groups:
            if not _is_studio_album(rg):
                continue
            title = rg.get("title", "")
            if title.strip().lower() in owned:
                continue

            existing = session.exec(
                select(PlexMissingAlbum).where(
                    PlexMissingAlbum.artist == artist.title,
                    PlexMissingAlbum.album == title,
                )
            ).first()
            if existing:
                existing.checked_at = existing.checked_at
                session.add(existing)
                continue

            session.add(
                PlexMissingAlbum(
                    artist=artist.title,
                    album=title,
                    release_group_mbid=rg.get("id"),
                    first_release_date=rg.get("first-release-date"),
                )
            )
            missing_count += 1

        # Commit after each artist rather than once at the end — a full
        # scan can take minutes, so this makes results show up on the Plex
        # Gaps page progressively instead of all at once (or not at all if
        # the process is interrupted mid-scan).
        session.commit()

    return missing_count

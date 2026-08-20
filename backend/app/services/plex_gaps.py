from __future__ import annotations

import logging
import re

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

# MusicBrainz release-group titles and Plex's own album titles frequently
# differ in ways that don't reflect a real difference in the album: a
# curly vs. straight apostrophe, or a MusicBrainz release group carrying an
# edition suffix ("Thriller (Special Edition)") for an album Plex just has
# tagged as the plain title. Stripping both down before comparing avoids
# flagging albums the user already owns as missing.
_EDITION_SUFFIX_RE = re.compile(
    r"[\(\[][^)\]]*\b(deluxe|remaster(ed)?|bonus(\s*tracks?)?|special\s*edition|"
    r"anniversary|expanded|reissue|explicit|edition)\b[^)\]]*[\)\]]",
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_title(title: str) -> str:
    title = _EDITION_SUFFIX_RE.sub("", title)
    title = _NON_WORD_RE.sub("", title)
    return _WHITESPACE_RE.sub(" ", title).strip().lower()


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

            owned_normalized = {_normalize_title(t) for t in get_artist_album_titles(artist)}
            release_groups = mb.get_artist_release_groups(mb_artist["id"])
        except Exception:  # noqa: BLE001 — one bad artist shouldn't abort the whole scan
            logger.warning("skipping %r during Plex gap scan", artist.title, exc_info=True)
            continue

        still_missing_titles = set()
        for rg in release_groups:
            if not _is_studio_album(rg):
                continue
            title = rg.get("title", "")
            if _normalize_title(title) in owned_normalized:
                continue
            still_missing_titles.add(title)

            existing = session.exec(
                select(PlexMissingAlbum).where(
                    PlexMissingAlbum.artist == artist.title,
                    PlexMissingAlbum.album == title,
                )
            ).first()
            if existing:
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

        # Clean up anything previously (mis)flagged as missing for this
        # artist that the improved matching above no longer considers a
        # real gap — otherwise a bad match from an earlier scan sticks
        # around forever even after the matching logic is fixed.
        stale = session.exec(
            select(PlexMissingAlbum).where(PlexMissingAlbum.artist == artist.title)
        ).all()
        for row in stale:
            if row.album not in still_missing_titles:
                session.delete(row)

        # Commit after each artist rather than once at the end — a full
        # scan can take minutes, so this makes results show up on the Plex
        # Gaps page progressively instead of all at once (or not at all if
        # the process is interrupted mid-scan).
        session.commit()

    return missing_count

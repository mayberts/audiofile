from __future__ import annotations

from typing import Optional

from plexapi.server import PlexServer

from ..config import Settings


class PlexNotConfigured(RuntimeError):
    pass


def connect_plex(url: str, token: str) -> PlexServer:
    if not url or not token:
        raise PlexNotConfigured("Plex URL/token are not configured yet")
    return PlexServer(url, token)


def get_plex_server(settings: Settings) -> PlexServer:
    return connect_plex(settings.plex_url, settings.plex_token)


def iter_music_artists(plex: PlexServer):
    for section in plex.library.sections():
        if section.type == "artist":
            yield from section.all()


def get_artist_album_titles(artist) -> set[str]:
    return {album.title.strip().lower() for album in artist.albums()}


def get_library_albums(plex: PlexServer) -> list[dict]:
    """Full album listing across every music library section.

    Uses the section-level albums() call (one paginated query per section)
    rather than iterating per-artist, since Plex already returns track count
    (leafCount) on each album without a further round-trip per album.
    """
    albums = []
    for section in plex.library.sections():
        if section.type != "artist":
            continue
        for album in section.albums():
            albums.append(
                {
                    "artist": album.parentTitle,
                    "album": album.title,
                    "year": album.year,
                    "track_count": getattr(album, "leafCount", None),
                }
            )
    return albums

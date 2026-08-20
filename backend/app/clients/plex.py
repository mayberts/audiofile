from __future__ import annotations

from typing import Optional

from plexapi.server import PlexServer

from ..config import Settings


class PlexNotConfigured(RuntimeError):
    pass


def get_plex_server(settings: Settings) -> PlexServer:
    if not settings.plex_url or not settings.plex_token:
        raise PlexNotConfigured("Plex URL/token are not configured yet")
    return PlexServer(settings.plex_url, settings.plex_token)


def iter_music_artists(plex: PlexServer):
    for section in plex.library.sections():
        if section.type == "artist":
            yield from section.all()


def get_artist_album_titles(artist) -> set[str]:
    return {album.title.strip().lower() for album in artist.albums()}

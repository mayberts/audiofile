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
                    # parentThumb is the artist's image, parentRatingKey is
                    # the artist's own item id (used to fetch its bio on
                    # demand), thumb is the album's own cover — all already
                    # included on each album's metadata, so none of this
                    # needs extra requests.
                    "artist_thumb": getattr(album, "parentThumb", None),
                    "artist_rating_key": str(album.parentRatingKey) if getattr(album, "parentRatingKey", None) else None,
                    "album": album.title,
                    "thumb": getattr(album, "thumb", None),
                    "year": album.year,
                    "track_count": getattr(album, "leafCount", None),
                    "rating_key": str(album.ratingKey),
                }
            )
    return albums


def get_artist_item(plex: PlexServer, rating_key: str):
    return plex.fetchItem(int(rating_key))


def get_artist_bio(plex: PlexServer, rating_key: str) -> str:
    artist = get_artist_item(plex, rating_key)
    return getattr(artist, "summary", None) or ""


def get_album_tracks(plex: PlexServer, rating_key: str) -> list[dict]:
    album = plex.fetchItem(int(rating_key))
    tracks = []
    for track in album.tracks():
        tracks.append(
            {
                "title": track.title,
                "track_number": track.index,
                "duration_ms": track.duration,
            }
        )
    return tracks

from __future__ import annotations

import re

from plexapi.server import PlexServer

from ..clients.musicbrainz import MusicBrainzClient
from ..clients.plex import get_artist_album_titles, get_artist_item

# Secondary/live/compilation-style release groups are rarely what someone
# means by "albums I don't have", so keep the default check to primary
# studio albums only.
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
# Hyphens/dashes act as word separators ("Self-Destruct" vs "Self Destruct"
# vs "Self–Destruct" are all the same words to a listener) so they become a
# space rather than being deleted outright — deleting them would otherwise
# collapse "Self-Destruct" into the single word "selfdestruct", which no
# longer matches a Plex tag spelled with a plain space.
_HYPHEN_RE = re.compile("[-‐‑‒–—―]")
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_title(title: str) -> str:
    title = _EDITION_SUFFIX_RE.sub("", title)
    title = _HYPHEN_RE.sub(" ", title)
    title = _NON_WORD_RE.sub("", title)
    return _WHITESPACE_RE.sub(" ", title).strip().lower()


def _title_variants(title: str) -> set[str]:
    """The full normalized title, plus (if the title has a ": subtitle"
    suffix) just the part before the colon — MusicBrainz sometimes carries
    the full official title ("Animal Ambition: An Untamed Desire to Win")
    for an album Plex just has tagged with the short marketing title
    ("Animal Ambition")."""
    variants = {_normalize_title(title)}
    if ":" in title:
        variants.add(_normalize_title(title.split(":", 1)[0]))
    return variants


def _is_studio_album(rg: dict) -> bool:
    secondary = set(rg.get("secondary-types", []))
    return not (secondary & SKIP_SECONDARY_TYPES)


def get_missing_albums_for_artist(plex: PlexServer, mb: MusicBrainzClient, artist_rating_key: str) -> list[dict]:
    """Studio albums MusicBrainz lists for this artist that aren't already
    in the Plex library — checked live, just for this one artist (two
    MusicBrainz requests), not a whole-library background scan."""
    artist = get_artist_item(plex, artist_rating_key)

    mb_artist = mb.search_artist(artist.title)
    if not mb_artist:
        return []

    owned_normalized = {_normalize_title(t) for t in get_artist_album_titles(artist)}
    release_groups = mb.get_artist_release_groups(mb_artist["id"])

    missing = []
    for rg in release_groups:
        if not _is_studio_album(rg):
            continue
        title = rg.get("title", "")
        if _title_variants(title) & owned_normalized:
            continue
        missing.append(
            {
                "album": title,
                "release_group_mbid": rg.get("id"),
                "first_release_date": rg.get("first-release-date"),
            }
        )
    return missing

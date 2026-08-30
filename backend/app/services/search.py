from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import PurePosixPath, PureWindowsPath

from ..config import Settings
from ..schemas import SearchFile
from .track_parsing import extract_track_title, normalize_title

AUDIO_EXTENSIONS = {"flac", "mp3", "m4a", "aac", "ogg", "wav", "alac"}
_WORD_RE = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _extension_of(filename: str) -> str:
    for cls in (PureWindowsPath, PurePosixPath):
        ext = cls(filename).suffix.lstrip(".").lower()
        if ext:
            return ext
    return ""


def parse_search_responses(raw_responses: list[dict]) -> list[SearchFile]:
    results: list[SearchFile] = []
    for user_block in raw_responses:
        username = user_block.get("username", "")
        has_slot = bool(user_block.get("hasFreeUploadSlot"))
        upload_speed = user_block.get("uploadSpeed")
        queue_length = user_block.get("queueLength")

        for f in user_block.get("files", []):
            filename = f.get("filename", "")
            ext = _extension_of(filename)
            if ext not in AUDIO_EXTENSIONS:
                continue
            results.append(
                SearchFile(
                    username=username,
                    filename=filename,
                    size=f.get("size", 0),
                    bitrate=f.get("bitRate"),
                    length_seconds=f.get("length"),
                    extension=ext,
                    slots_free=has_slot,
                    upload_speed=upload_speed,
                    queue_length=queue_length,
                    score=0.0,
                )
            )

    for r in results:
        r.score = score_result(r)
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def score_result(
    r: SearchFile,
    preferred_formats: list[str] | None = None,
    min_bitrate_kbps: int = 0,
) -> float:
    preferred_formats = preferred_formats or ["flac", "mp3"]
    score = 0.0

    if r.extension in preferred_formats:
        # Earlier entries in the preferred list score higher.
        score += (len(preferred_formats) - preferred_formats.index(r.extension)) * 20

    if r.slots_free:
        score += 30
    else:
        score -= 40

    if r.bitrate:
        if r.bitrate >= min_bitrate_kbps:
            score += min(r.bitrate / 32, 20)
        else:
            score -= 25

    if r.upload_speed:
        score += min(r.upload_speed / 100_000, 15)

    if r.queue_length:
        score -= min(r.queue_length * 0.5, 10)

    return round(score, 2)


def _peer_priority(sample: SearchFile) -> tuple[bool, int, int]:
    """Tiebreak ahead of file-quality score: a peer with no free upload
    slot won't even start the transfer, and a long queue or slow upload
    speed can leave a download sitting for ages regardless of how good the
    file itself looks. Sorted descending, so higher is better throughout —
    queue length is negated so a shorter queue sorts first."""
    return (sample.slots_free, -(sample.queue_length or 0), sample.upload_speed or 0)


def _meets_quality_bar(r: SearchFile, settings: Settings) -> bool:
    """Whether a file actually satisfies the configured quality preferences
    (Settings > Preferred formats / Minimum bitrate) — not just scores well
    on them. score_result() only ever rewards matching those, it never
    penalizes missing them, so on its own a non-preferred-format file with
    a great slot/queue/speed could still outscore a preferred-format file
    with a merely-okay one. Used to filter to a "preferred" pool before
    ranking by completeness/peer reliability, so e.g. a flac-only setting
    doesn't quietly get overridden by a faster mp3 peer."""
    preferred = settings.preferred_format_list
    if preferred and r.extension not in preferred:
        return False
    if r.bitrate is not None and r.bitrate < settings.min_bitrate_kbps:
        return False
    return True


def best_match(
    results: list[SearchFile], settings: Settings
) -> SearchFile | None:
    if not results:
        return None
    # Prefer files that actually meet the configured format/bitrate bar;
    # only fall back to everything if nothing does (better than nothing).
    qualifying = [r for r in results if _meets_quality_bar(r, settings)]
    pool = qualifying or results
    rescored = sorted(
        pool,
        key=lambda r: (
            _peer_priority(r),
            score_result(r, settings.preferred_format_list, settings.min_bitrate_kbps),
        ),
        reverse=True,
    )
    return rescored[0]


def group_by_folder(results: list[SearchFile]) -> dict[tuple[str, str], list[SearchFile]]:
    """Soulseek search hits are individual files; the directory a user's
    file lives in is the closest thing to "this is one album share" — group
    on (username, directory) so an album want can grab everything one
    person has in one folder instead of the single best-scored file
    across everyone."""
    groups: dict[tuple[str, str], list[SearchFile]] = {}
    for r in results:
        directory = str(PureWindowsPath(r.filename).parent)
        groups.setdefault((r.username, directory), []).append(r)
    return groups


def dedupe_by_title(files: list[SearchFile], artist: str, album: str | None) -> list[SearchFile]:
    """Collapses duplicate versions of the same track within a single
    folder down to the best-scoring copy, keyed on the track title
    extracted from each filename — at most one file per extracted title.

    This is a scoring aid only (see score_album_candidates's count_ratio
    and confidence terms) -- it must never again be used to decide which
    files actually get downloaded. audiofile used to trim a folder down to
    its deduped set before grabbing it; that made duplicate rejection only
    as good as filename-title matching, which a clean/explicit pair or any
    other differently-named duplicate slips right past. The whole folder
    is downloaded unfiltered now, and real duplicate rejection happens at
    import time instead, against MusicBrainz's own canonical tracklist
    position (see process_completed_download in services/downloads.py),
    which isn't fooled by filename drift the way title-string comparison
    is. A file whose title can't be extracted at all is kept as-is rather
    than risk merging unrelated files under a shared empty key."""
    best_by_title: dict[str, SearchFile] = {}
    order: list[str] = []
    for f in files:
        title = normalize_title(extract_track_title(f.filename, artist, album))
        if not title:
            key = f"__unkeyed_{len(order)}__"
            order.append(key)
            best_by_title[key] = f
            continue
        current = best_by_title.get(title)
        if current is None:
            order.append(title)
        if current is None or f.score > current.score:
            best_by_title[title] = f
    return [best_by_title[key] for key in order]


@dataclass
class ScoredFolder:
    username: str
    directory: str
    # The FULL, unfiltered contents of this (username, directory) share --
    # score_album_candidates only ever ranks candidates, it never trims a
    # folder's own file list. Whichever candidate the caller acts on gets
    # downloaded exactly as found.
    files: list[SearchFile]
    score: float
    tier: str  # "auto" | "manual" | "rejected"


# score_album_candidates' tier gate. "auto" additionally requires artist
# evidence in the folder path (see _has_artist_evidence) -- a coherent-
# looking folder that happens to lack the artist's name anywhere in its
# path is more likely a mislabeled share than the album actually wanted.
_AUTO_TIER_THRESHOLD = 0.72
_MANUAL_TIER_THRESHOLD = 0.35
# Mirrors DroppedNeedle's own coherence/confidence split -- coherence
# (does this folder look like a complete, well-formed copy of the album?)
# is weighted higher than confidence (do the individual tracks' titles
# actually match?) because confidence degrades to a neutral guess whenever
# there's no MusicBrainz tracklist to compare against, while coherence's
# quality/count signals are almost always available.
_COHERENCE_WEIGHT = 0.625
_CONFIDENCE_WEIGHT = 0.375


def _has_artist_evidence(directory: str, artist: str) -> bool:
    norm_artist = normalize_title(artist)
    return bool(norm_artist) and norm_artist in normalize_title(directory)


def _file_title_confidence(
    f: SearchFile, artist: str, album: str | None, release_tracks: list[dict]
) -> float:
    """How well one file's filename-derived title matches the release's
    own tracklist -- 1.0 for an exact (normalized) match, otherwise the
    best fuzzy text ratio against any track title, with duration proximity
    (SearchFile.length_seconds vs. a track's length_ms) as a secondary
    signal for when the title text itself doesn't line up well (a
    differently-named rip of the same recording, say)."""
    title = normalize_title(extract_track_title(f.filename, artist, album))
    if not title:
        return 0.6

    best = 0.0
    for t in release_tracks:
        track_title = normalize_title(t.get("title") or "")
        if not track_title:
            continue
        if title == track_title:
            return 1.0
        best = max(best, SequenceMatcher(None, title, track_title).ratio())

    if best < 0.6 and f.length_seconds:
        for t in release_tracks:
            length_ms = t.get("length_ms")
            if not length_ms:
                continue
            diff = abs(f.length_seconds - length_ms / 1000)
            if diff <= 3:
                best = max(best, 0.85)
            elif diff <= 8:
                best = max(best, 0.6)
    return best


def score_album_candidates(
    results: list[SearchFile],
    settings: Settings,
    artist: str,
    album: str,
    expected_track_count: int | None = None,
    release_tracks: list[dict] | None = None,
    min_tracks: int = 2,
) -> list[ScoredFolder]:
    """Scores every (username, directory) share that looks like it could be
    this album -- a coherence + confidence blend modeled on DroppedNeedle's
    AlbumPreflightScorer, gated into "auto" / "manual" / "rejected" tiers
    instead of picking a single algorithmic winner outright.

    coherence asks "does this folder look like a complete, well-formed copy
    of the album?" -- a blend of unique-track-count vs. expected_track_count
    (via dedupe_by_title, so a folder padded with duplicate copies of the
    same tracks doesn't score as more complete than it really is), how well
    the folder name matches "{artist} {album}", and what fraction of its
    files meet the configured format/bitrate bar.

    confidence asks "do the individual tracks actually match this release?"
    -- see _file_title_confidence. It's neutral (0.6, neither damning nor
    vouching) when no release_tracks are available at all, since there's
    nothing to compare titles against.

    Every folder clearing min_tracks is returned, sorted by score
    descending, including "rejected"-tier ones -- callers decide what to do
    based on the top candidate's tier: "auto" proceeds automatically,
    "manual" means nothing was confident enough to trust unattended and the
    candidates should be pooled for a human to pick from, and "rejected" (or
    an empty list) means fall back the way an empty best-effort search
    always has -- a single-file best_match, then not-found."""
    groups = group_by_folder(results)
    target_words = _words(f"{artist} {album}") if album else set()

    candidates: list[ScoredFolder] = []
    for (username, directory), files in groups.items():
        if len(files) < min_tracks:
            continue

        unique_tracks = dedupe_by_title(files, artist, album)
        count_ratio = min(len(unique_tracks) / expected_track_count, 1.0) if expected_track_count else 0.7

        # What fraction of the artist+album's own words show up somewhere in
        # the folder path -- a word-overlap measure, not a raw character
        # similarity ratio: normalize_title strips spaces entirely (it's
        # built for comparing single track titles, where that's harmless),
        # so running a whole folder PATH through it first and then fuzzy-
        # matching the squashed result loses word-boundary information and
        # gives misleadingly high scores to unrelated text that merely
        # shares a lot of letters.
        name_similarity = (
            len(_words(directory) & target_words) / len(target_words) if target_words else 0.5
        )

        quality_consistency = sum(1 for f in files if _meets_quality_bar(f, settings)) / len(files)

        coherence = (count_ratio + name_similarity + quality_consistency) / 3

        if release_tracks:
            confidences = [_file_title_confidence(f, artist, album, release_tracks) for f in unique_tracks]
            confidence = sum(confidences) / len(confidences) if confidences else 0.6
        else:
            confidence = 0.6

        score = round(_COHERENCE_WEIGHT * coherence + _CONFIDENCE_WEIGHT * confidence, 4)

        # A folder whose path shares literally none of the artist+album's
        # words is never worth showing, no matter how good its bitrate or
        # file count happen to look -- those two signals alone can
        # otherwise drag an entirely-wrong-artist folder's score up over
        # the "manual" floor, which would mean showing someone an
        # obviously-irrelevant option in their review queue.
        no_name_relevance = bool(target_words) and not (_words(directory) & target_words)

        if score >= _AUTO_TIER_THRESHOLD and _has_artist_evidence(directory, artist):
            tier = "auto"
        elif score >= _MANUAL_TIER_THRESHOLD and not no_name_relevance:
            tier = "manual"
        else:
            tier = "rejected"

        candidates.append(
            ScoredFolder(username=username, directory=directory, files=files, score=score, tier=tier)
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from ..config import Settings
from ..schemas import SearchFile
from .track_parsing import extract_track_title, normalize_title

AUDIO_EXTENSIONS = {"flac", "mp3", "m4a", "aac", "ogg", "wav", "alac"}


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


def filter_files_matching_titles(
    files: list[SearchFile], artist: str, album: str | None, expected_titles: list[str]
) -> list[SearchFile]:
    """Keeps only the files whose extracted track title matches one of
    `expected_titles` (a specific release's own tracklist, picked by hand
    via the release-editions picker) -- at most one file per expected
    title, the best-scoring one if several files claim the same title.

    A single peer's shared folder can bundle more than one edition of the
    same album together (a plain rip alongside a full set of "(extended
    mix)" versions, say) -- picking "the biggest folder" or even "the
    folder closest to the expected track count" still grabs the whole
    bundle when that's the only (or largest) folder available, since
    there's nothing smaller to prefer it over. Matching by title instead
    finds exactly the tracks that are actually wanted regardless of what
    else happens to be sitting alongside them in the same share."""
    wanted = {normalize_title(t) for t in expected_titles if t}
    if not wanted:
        return files

    best_by_title: dict[str, SearchFile] = {}
    for f in files:
        title = normalize_title(extract_track_title(f.filename, artist, album))
        if title not in wanted:
            continue
        current = best_by_title.get(title)
        if current is None or f.score > current.score:
            best_by_title[title] = f
    return list(best_by_title.values())


def dedupe_by_title(files: list[SearchFile], artist: str, album: str | None) -> list[SearchFile]:
    """Collapses duplicate versions of the same track within a single
    folder down to the best-scoring copy, keyed on the track title
    extracted from each filename — at most one file per extracted title.

    Runs unconditionally on every folder best_album_folder considers, not
    just ones matched against a pinned release's tracklist: a peer's share
    that bundles two rips of the same album, a clean/explicit pair, or a
    bonus disc duplicating tracks from the main one still has only one
    filename-derived title per real track, so this catches those
    duplicates even when there's no MusicBrainz tracklist to compare
    against. A file whose title can't be extracted at all is kept as-is
    rather than risk merging unrelated files under a shared empty key."""
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


def best_album_folder(
    results: list[SearchFile],
    settings: Settings,
    min_tracks: int = 2,
    expected_titles: list[str] | None = None,
    artist: str = "",
    album: str | None = None,
) -> list[SearchFile] | None:
    """Picks the (username, folder) that looks most like a complete album
    share in the preferred format/bitrate, rather than a single stray file
    or a "good enough" folder in the wrong format.

    When a specific release was pinned by hand (expected_titles set), every
    candidate file is first narrowed down to just the ones matching one of
    that release's own track titles — see filter_files_matching_titles for
    why that's needed instead of just comparing folder sizes. Grouping and
    ranking then run on that narrowed set, so a peer's folder that mixes in
    bonus/extended-mix tracks alongside the plain ones only ever contributes
    its matching tracks, not the whole bundle.

    Every candidate folder — pinned release or not — is then run through
    dedupe_by_title, keeping only the best-scoring file per extracted track
    title. Without this, a folder mixing two versions of the same songs
    (two rips, clean/explicit, a bonus disc) with no pinned release to
    filter against would still get grabbed whole: nothing else here
    compares titles within a folder, only counts and quality scores. This
    also means folder ranking below sees each folder's real track count
    (unique titles), not an inflated file count from bundled duplicates.

    Folders where every file meets the configured quality bar are
    considered first and exclusively — a flac-only setting means an mp3
    folder never gets picked over a flac one just for having a shorter
    queue, even though nothing here is comparing them file-by-file. Only
    if no folder clears the bar at all does the full candidate set get
    considered, so a real album is still preferred over nothing.

    Within whichever pool is used: most tracks first (a complete album, or
    a more complete match against a pinned release's tracklist, beats a
    partial one), then — among folders tied on track count — the peer
    least likely to leave the download stuck (free slot, short queue, fast
    upload), then best average file-quality score as a final tiebreak.
    Returns None if nothing has at least min_tracks files, so the caller
    can fall back to a single-file best_match instead of forcing a "whole
    folder" result out of scraps."""
    if expected_titles:
        results = filter_files_matching_titles(results, artist, album, expected_titles)

    groups = group_by_folder(results)
    deduped_groups = {key: dedupe_by_title(files, artist, album) for key, files in groups.items()}
    all_candidates = [files for files in deduped_groups.values() if len(files) >= min_tracks]
    if not all_candidates:
        return None

    qualifying = [files for files in all_candidates if all(_meets_quality_bar(f, settings) for f in files)]
    pool = qualifying or all_candidates

    def group_key(files: list[SearchFile]) -> tuple:
        scores = [score_result(f, settings.preferred_format_list, settings.min_bitrate_kbps) for f in files]
        avg_quality = sum(scores) / len(scores)
        return (len(files), *_peer_priority(files[0]), avg_quality)

    pool.sort(key=group_key, reverse=True)
    return pool[0]

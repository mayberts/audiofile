from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from ..config import Settings
from ..schemas import SearchFile

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


def best_match(
    results: list[SearchFile], settings: Settings
) -> SearchFile | None:
    if not results:
        return None
    rescored = sorted(
        results,
        key=lambda r: score_result(r, settings.preferred_format_list, settings.min_bitrate_kbps),
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


def best_album_folder(
    results: list[SearchFile], settings: Settings, min_tracks: int = 2
) -> list[SearchFile] | None:
    """Picks the (username, folder) that looks most like a complete album
    share rather than a single stray file: most tracks first, then best
    average quality score. Returns None if nothing has at least min_tracks
    files, so the caller can fall back to a single-file best_match instead
    of forcing a "whole folder" result out of scraps."""
    groups = group_by_folder(results)
    candidates = [files for files in groups.values() if len(files) >= min_tracks]
    if not candidates:
        return None

    def group_key(files: list[SearchFile]) -> tuple[int, float]:
        scores = [score_result(f, settings.preferred_format_list, settings.min_bitrate_kbps) for f in files]
        return (len(files), sum(scores) / len(scores))

    candidates.sort(key=group_key, reverse=True)
    return candidates[0]

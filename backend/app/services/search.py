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

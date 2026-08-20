from __future__ import annotations

from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

from ..clients.musicbrainz import TrackMetadata


def tag_file(path: Path, meta: TrackMetadata, cover_bytes: bytes | None) -> None:
    ext = path.suffix.lower()
    if ext == ".mp3":
        _tag_mp3(path, meta, cover_bytes)
    elif ext == ".flac":
        _tag_flac(path, meta, cover_bytes)
    elif ext in (".m4a", ".mp4", ".aac"):
        _tag_mp4(path, meta, cover_bytes)
    # Other formats (e.g. .ogg, .wav) are left untagged for now; mutagen
    # support exists but is out of scope for the MVP tagging pass.


def _tag_mp3(path: Path, meta: TrackMetadata, cover_bytes: bytes | None) -> None:
    try:
        tags = EasyID3(path)
    except ID3NoHeaderError:
        tags = EasyID3()
        tags.save(path)
        tags = EasyID3(path)

    tags["artist"] = meta.artist
    tags["album"] = meta.album
    tags["title"] = meta.title
    if meta.track_number:
        tags["tracknumber"] = str(meta.track_number)
    if meta.year:
        tags["date"] = meta.year
    if meta.genre:
        tags["genre"] = meta.genre
    tags.save(path)

    if cover_bytes:
        id3 = ID3(path)
        id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_bytes))
        id3.save(path)


def _tag_flac(path: Path, meta: TrackMetadata, cover_bytes: bytes | None) -> None:
    audio = FLAC(path)
    audio["artist"] = meta.artist
    audio["album"] = meta.album
    audio["title"] = meta.title
    if meta.track_number:
        audio["tracknumber"] = str(meta.track_number)
    if meta.year:
        audio["date"] = meta.year
    if meta.genre:
        audio["genre"] = meta.genre

    if cover_bytes:
        audio.clear_pictures()
        pic = Picture()
        pic.data = cover_bytes
        pic.type = 3
        pic.mime = "image/jpeg"
        audio.add_picture(pic)

    audio.save()


def _tag_mp4(path: Path, meta: TrackMetadata, cover_bytes: bytes | None) -> None:
    audio = MP4(path)
    audio["\xa9ART"] = meta.artist
    audio["\xa9alb"] = meta.album
    audio["\xa9nam"] = meta.title
    if meta.track_number:
        audio["trkn"] = [(meta.track_number, meta.total_tracks or 0)]
    if meta.year:
        audio["\xa9day"] = meta.year
    if meta.genre:
        audio["\xa9gen"] = meta.genre

    if cover_bytes:
        audio["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]

    audio.save()


def probe_bitrate_kbps(path: Path) -> int | None:
    audio = MutagenFile(path)
    if audio is None or not getattr(audio, "info", None):
        return None
    bitrate = getattr(audio.info, "bitrate", None)
    return int(bitrate / 1000) if bitrate else None

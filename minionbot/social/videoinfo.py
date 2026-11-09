"""Read width/height/duration from an MP4 file (pure Python, no ffmpeg needed).

Telegram guesses a video's shape unless width/height are sent with it; guessing wrong shows
landscape videos in the wrong aspect ratio.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

CONTAINER_BOXES = {b"moov", b"trak", b"mdia"}


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration: int  # seconds


def IterBoxes(handle: BinaryIO, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """Yield (type, payloadStart, payloadEnd) for the boxes between start and end."""
    position = start
    while position + 8 <= end:
        handle.seek(position)
        header = handle.read(8)
        if len(header) < 8:
            return
        size, boxType = struct.unpack(">I4s", header)
        headerSize = 8
        if size == 1:
            size = struct.unpack(">Q", handle.read(8))[0]
            headerSize = 16
        elif size == 0:
            size = end - position
        if size < headerSize:
            return
        yield boxType, position + headerSize, min(position + size, end)
        position += size


def ReadAt(handle: BinaryIO, offset: int, fmt: str):
    handle.seek(offset)
    return struct.unpack(fmt, handle.read(struct.calcsize(fmt)))


def ReadMovieHeader(handle: BinaryIO, payload: int) -> tuple[int, int]:
    version = ReadAt(handle, payload, ">B")[0]
    if version == 1:
        timescale, duration = ReadAt(handle, payload + 4 + 16, ">IQ")
    else:
        timescale, duration = ReadAt(handle, payload + 4 + 8, ">II")
    return timescale, duration


def ReadTrackHeader(handle: BinaryIO, payload: int) -> tuple[int, int]:
    """Presentation size of a track, with the rotation matrix applied (phone videos are often rotated)."""
    version = ReadAt(handle, payload, ">B")[0]
    base = payload + 4 + (32 if version == 1 else 20) + 16  # skip times/id/duration, reserved, layer, volume
    a, b, _, c, d = ReadAt(handle, base, ">5i")
    width, height = ReadAt(handle, base + 36, ">II")
    width, height = width / 65536, height / 65536
    if a == 0 and d == 0 and b != 0 and c != 0:  # rotated by 90 or 270 degrees
        width, height = height, width
    return round(width), round(height)


def ReadMp4Info(path: str | Path) -> VideoInfo | None:
    """Dimensions and duration of the first video track, or None if the file can't be understood."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            fileEnd = handle.tell()
            timescale = duration = 0
            size: tuple[int, int] | None = None
            for boxType, payload, end in IterBoxes(handle, 0, fileEnd):
                if boxType != b"moov":
                    continue
                for innerType, innerPayload, innerEnd in IterBoxes(handle, payload, end):
                    if innerType == b"mvhd":
                        timescale, duration = ReadMovieHeader(handle, innerPayload)
                    elif innerType == b"trak" and size is None:
                        size = ReadVideoTrack(handle, innerPayload, innerEnd)
            if size is None or size[0] <= 0 or size[1] <= 0:
                return None
            seconds = round(duration / timescale) if timescale else 0
            return VideoInfo(size[0], size[1], max(seconds, 0))
    except (OSError, struct.error):
        return None


def ReadVideoTrack(handle: BinaryIO, start: int, end: int) -> tuple[int, int] | None:
    size = None
    isVideo = False
    for boxType, payload, boxEnd in IterBoxes(handle, start, end):
        if boxType == b"tkhd":
            size = ReadTrackHeader(handle, payload)
        elif boxType == b"mdia":
            for innerType, innerPayload, _ in IterBoxes(handle, payload, boxEnd):
                if innerType == b"hdlr":
                    isVideo = ReadAt(handle, innerPayload + 8, ">4s")[0] == b"vide"
    return size if isVideo else None

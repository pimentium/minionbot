import struct
from pathlib import Path

from minionbot.social.videoinfo import ReadMp4Info, VideoInfo

DATA = Path(__file__).parent / "data"
# Fixtures were generated with ffmpeg and checked against ffprobe:
# landscape 640x360 3s, portrait 360x640 2s, rotated = 640x360 stored with a 90 degree display rotation (shown as 360x640).


def test_landscape_video():
    assert ReadMp4Info(DATA / "landscape.mp4") == VideoInfo(640, 360, 3)


def test_portrait_video():
    assert ReadMp4Info(DATA / "portrait.mp4") == VideoInfo(360, 640, 2)


def test_rotation_swaps_dimensions():
    assert ReadMp4Info(DATA / "rotated.mp4") == VideoInfo(360, 640, 3)


def test_garbage_and_missing_files(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not an mp4 at all")
    assert ReadMp4Info(bad) is None
    assert ReadMp4Info(tmp_path / "missing.mp4") is None


def test_truncated_file(tmp_path):
    truncated = tmp_path / "t.mp4"
    truncated.write_bytes((DATA / "landscape.mp4").read_bytes()[:40])
    assert ReadMp4Info(truncated) is None


def test_audio_only_returns_none(tmp_path):
    def Box(kind, payload):
        return struct.pack(">I4s", 8 + len(payload), kind) + payload

    hdlr = Box(b"hdlr", struct.pack(">II4s", 0, 0, b"soun") + b"\0" * 12)
    tkhd = Box(b"tkhd", b"\0" * 84 + struct.pack(">II", 100 << 16, 100 << 16))
    movie = Box(b"moov", Box(b"trak", tkhd + Box(b"mdia", hdlr)))
    path = tmp_path / "a.mp4"
    path.write_bytes(movie)
    assert ReadMp4Info(path) is None

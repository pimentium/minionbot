from pathlib import Path

from minionbot.social.format import CAPTION_LIMIT, MESSAGE_LIMIT, BuildTexts, ChunkText
from minionbot.social.models import PHOTO, LocalMedia, Post


def MakePost(text="hello", media=1, notes=None, author="@alice"):
    return Post("twitter", "u", author, text, [LocalMedia(Path("x.jpg"), PHOTO)] * media, notes or [])


def test_short_text_becomes_caption():
    caption, texts = BuildTexts(MakePost())
    assert caption == "Twitter/X · @alice\n\nhello" and texts == []


def test_long_text_goes_to_messages_with_header_caption():
    caption, texts = BuildTexts(MakePost(text="a " * 1000))
    assert caption == "Twitter/X · @alice"
    assert all(len(t) <= MESSAGE_LIMIT for t in texts) and "".join(texts).replace(" ", "").startswith("aaa")


def test_no_media_all_text():
    caption, texts = BuildTexts(MakePost(media=0))
    assert caption == "" and texts == ["Twitter/X · @alice\n\nhello"]


def test_notes_included():
    caption, _ = BuildTexts(MakePost(notes=["Skipped a video"]))
    assert "Skipped a video" in caption


def test_no_author_and_no_text():
    caption, _ = BuildTexts(MakePost(text="", author=""))
    assert caption == "Twitter/X"


def test_chunk_text_respects_limit_and_loses_nothing():
    text = " ".join(f"word{i}" for i in range(3000))
    chunks = ChunkText(text, 500)
    assert all(len(c) <= 500 for c in chunks) and " ".join(chunks) == text


def test_caption_never_exceeds_limit():
    caption, _ = BuildTexts(MakePost(author="@" + "x" * 2000))
    assert len(caption) <= CAPTION_LIMIT

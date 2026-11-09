"""Turn a downloaded Post into Telegram-sized texts."""

from __future__ import annotations

from minionbot.social.models import NETWORK_LABELS, Post

CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4000  # Telegram allows 4096; leave headroom


def Header(post: Post) -> str:
    label = NETWORK_LABELS.get(post.network, post.network)
    return f"{label} · {post.author}" if post.author else label


def ChunkText(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    chunks: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def BuildTexts(post: Post) -> tuple[str, list[str]]:
    """Return (caption for the first media item or '', follow-up text messages).

    With media: the whole text becomes the caption when it fits, otherwise the caption is just the
    header and the text follows as separate messages. Without media everything is text messages.
    """
    body = "\n\n".join(part for part in [post.text, "\n".join(post.notes)] if part)
    full = f"{Header(post)}\n\n{body}" if body else Header(post)
    if not post.media:
        return "", ChunkText(full)
    if len(full) <= CAPTION_LIMIT:
        return full, []
    return Header(post)[:CAPTION_LIMIT], ChunkText(body)

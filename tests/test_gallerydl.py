import json
import subprocess
from types import SimpleNamespace

import pytest

from minionbot.social import gallerydl
from minionbot.social.gallerydl import BuildConfig, GalleryDlDownloader, MergeTwitterThread, ParseGeneric, Entries
from minionbot.social.models import PHOTO, VIDEO, DownloadError, SocialAccount

AUTHOR = {"id": "7", "name": "alice", "nick": "Alice A"}
OTHER = {"id": "8", "name": "bob", "nick": "Bob"}


def TweetEntry(tweetId, text, media, author=AUTHOR, replyId=None, conversationId=100, extension="jpg"):
    meta = {"tweet_id": tweetId, "content": text, "author": author, "reply_id": replyId, "conversation_id": conversationId, "extension": extension}
    return (media or "text:", meta)


def Output(entries):
    return json.dumps([[2, {}]] + [[3, url, meta] for url, meta in entries])


def test_thread_parts_are_combined_in_order():
    entries = [
        TweetEntry(102, "part 3", "https://pbs.twimg.com/media/c?format=jpg&name=orig", replyId=101),
        TweetEntry(100, "part 1", "https://pbs.twimg.com/media/a?format=jpg&name=orig"),
        TweetEntry(101, "part 2", None, replyId=100),
        TweetEntry(150, "a reply from someone", None, author=OTHER, replyId=100),
        TweetEntry(160, "author replying to someone else", None, replyId=150),
        TweetEntry(103, "video", "https://video.twimg.com/v.mp4", replyId=102, extension="mp4"),
    ]
    post = MergeTwitterThread("https://x.com/alice/status/101", entries)
    assert post.text == "part 1\n\npart 2\n\npart 3\n\nvideo"
    assert [m.kind for m in post.media] == [PHOTO, PHOTO, VIDEO]
    assert post.author == "Alice A (@alice)"


def test_single_tweet_text_only():
    post = MergeTwitterThread("https://x.com/alice/status/100", [TweetEntry(100, "just text", None)])
    assert post.text == "just text" and post.media == []


def test_multiple_images_in_one_tweet_deduped():
    url1, url2 = "https://pbs.twimg.com/media/a?format=jpg", "https://pbs.twimg.com/media/b?format=png"
    post = MergeTwitterThread("https://x.com/alice/status/100", [TweetEntry(100, "t", url1), TweetEntry(100, "t", url2), TweetEntry(100, "t", url1)])
    assert [m.url for m in post.media] == [url1, url2]


def test_retweets_are_ignored_and_empty_raises():
    entry = ("https://x/a.jpg", {"tweet_id": 1, "retweet_id": 5, "author": AUTHOR})
    with pytest.raises(DownloadError):
        MergeTwitterThread("https://x.com/a/status/1", [entry])


def test_generic_parse_facebook_like():
    entries = [
        ("https://scontent.fb/photo1.jpg", {"username": "Carol", "caption": "hi", "extension": "jpg"}),
        ("https://scontent.fb/video.mp4", {"username": "Carol", "extension": "mp4"}),
        ("ytdl:https://facebook.com/x", {"username": "Carol"}),
    ]
    post = ParseGeneric("facebook", "u", entries)
    assert (post.author, post.text) == ("Carol", "hi")
    assert [m.kind for m in post.media] == [PHOTO, VIDEO]


def test_entries_rejects_garbage():
    with pytest.raises(DownloadError):
        Entries("not json")


def test_build_config_uses_cookies_only():
    config = BuildConfig("twitter", SocialAccount(cookiesFile="/c.txt"), conversations=True)
    assert config["extractor"]["twitter"] == {"conversations": True, "text-tweets": True, "cookies": "/c.txt"}
    assert BuildConfig("facebook", None) == {"extractor": {"facebook": {}}}
    assert BuildConfig("instagram", SocialAccount(cookiesFile="/i.txt"))["extractor"]["instagram"] == {"cookies": "/i.txt"}


def test_twitter_auth_token_becomes_cookie():
    options = BuildConfig("twitter", SocialAccount(authToken="TOKEN"), conversations=True)["extractor"]["twitter"]
    assert options["cookies"] == {"auth_token": "TOKEN"}


def FakeRun(stdout="", returncode=0, capture=None):
    def Run(command, **kwargs):
        if capture is not None:
            capture["command"] = command
            capture["config"] = json.loads(open(command[command.index("-c") + 1]).read())
        return SimpleNamespace(stdout=stdout, stderr="boom", returncode=returncode)

    return Run


def test_fetch_runs_subprocess_without_secrets_on_command_line(monkeypatch):
    captured = {}
    stdout = Output([("https://pbs.twimg.com/media/a?format=jpg", {"tweet_id": 5, "content": "x", "author": AUTHOR, "conversation_id": 5, "extension": "jpg"})])
    monkeypatch.setattr(gallerydl.subprocess, "run", FakeRun(stdout, capture=captured))
    post = GalleryDlDownloader("twitter", SocialAccount(authToken="S3CRET")).Fetch("https://x.com/alice/status/5")
    assert post.media and "S3CRET" not in " ".join(captured["command"])
    assert captured["config"]["extractor"]["twitter"]["cookies"] == {"auth_token": "S3CRET"}
    assert "-j" in captured["command"]


def test_fetch_failure_and_timeout(monkeypatch):
    monkeypatch.setattr(gallerydl.subprocess, "run", FakeRun("", returncode=1))
    with pytest.raises(DownloadError):
        GalleryDlDownloader("facebook").Fetch("https://facebook.com/x/posts/1")

    def Timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("gallery-dl", 1)

    monkeypatch.setattr(gallerydl.subprocess, "run", Timeout)
    with pytest.raises(DownloadError, match="Timed out"):
        GalleryDlDownloader("facebook").Fetch("https://facebook.com/x/posts/1")


def test_generic_nothing_found(monkeypatch):
    monkeypatch.setattr(gallerydl.subprocess, "run", FakeRun("[]"))
    with pytest.raises(DownloadError):
        GalleryDlDownloader("instagram").Fetch("https://instagram.com/p/A/")


# Shapes below were captured from a real anonymous gallery-dl run (jack's first tweet, text only).
AUTH_ERROR = json.dumps([[-1, {"error": "AuthRequired", "message": "authenticated cookies needed to access this timeline"}]])
TEXT_ONLY = json.dumps([[2, {"tweet_id": 20, "content": "just setting up my twttr", "conversation_id": 20, "reply_id": 0, "retweet_id": 0, "author": {"id": 12, "name": "jack", "nick": "jack"}}]])


def SequencedRun(outputs, configs):
    calls = iter(outputs)

    def Run(command, **kwargs):
        configs.append(json.loads(open(command[command.index("-c") + 1]).read()))
        return SimpleNamespace(stdout=next(calls), stderr="", returncode=0)

    return Run


def test_text_only_tweet_anonymous(monkeypatch):
    configs = []
    monkeypatch.setattr(gallerydl.subprocess, "run", SequencedRun([TEXT_ONLY], configs))
    post = GalleryDlDownloader("twitter").Fetch("https://x.com/jack/status/20")
    assert post.text == "just setting up my twttr" and post.author == "jack (@jack)" and post.media == []
    assert "conversations" not in configs[0]["extractor"]["twitter"]


def test_logged_in_failure_falls_back_to_single_tweet_without_credentials(monkeypatch):
    configs = []
    monkeypatch.setattr(gallerydl.subprocess, "run", SequencedRun([AUTH_ERROR, TEXT_ONLY], configs))
    post = GalleryDlDownloader("twitter", SocialAccount(authToken="tok")).Fetch("https://x.com/jack/status/20")
    assert configs[0]["extractor"]["twitter"]["conversations"] is True
    assert "cookies" not in configs[1]["extractor"]["twitter"] and "conversations" not in configs[1]["extractor"]["twitter"]
    assert post.text == "just setting up my twttr" and "thread" in post.notes[0]


def test_twitter_total_failure(monkeypatch):
    monkeypatch.setattr(gallerydl.subprocess, "run", SequencedRun([AUTH_ERROR], []))
    with pytest.raises(DownloadError):
        GalleryDlDownloader("twitter").Fetch("https://x.com/jack/status/20")

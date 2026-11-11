import json
import os
import stat

import httpx
import pytest

from minionbot.web.archive import Wayback
from minionbot.web.errors import UnexpectedWebError, WebError
from minionbot.web.fetcher import PageFetcher
from minionbot.web.service import NOTHING_READABLE, WebService
from minionbot.web.telegraph import CONTENT_LIMIT_BYTES, TelegraphClient

from tests.web_fixtures import PARAGRAPHS, TEASER, Article

URL = "https://news.example.com/riverfront"


# ---- Telegraph ------------------------------------------------------------------------------------------

class TelegraphApi:
    def __init__(self, createPageError=None):
        self.calls, self.createPageError = [], createPageError

    def __call__(self, request):
        if request.method == "GET":  # the readability check of the new page
            self.pageChecks = getattr(self, "pageChecks", 0) + 1
            status = self.pageStatuses.pop(0) if getattr(self, "pageStatuses", None) else 200
            return httpx.Response(status, text="<html></html>")
        method, payload = request.url.path.strip("/"), json.loads(request.content)
        self.calls.append((method, payload))
        if method == "createAccount":
            return httpx.Response(200, json={"ok": True, "result": {"access_token": "TOKEN-1"}})
        if self.createPageError:
            return httpx.Response(200, json={"ok": False, "error": self.createPageError})
        return httpx.Response(200, json={"ok": True, "result": {"url": "https://telegra.ph/Council-11-09"}})


def Client(api, tmp_path, token=""):
    return TelegraphClient(tmp_path / "data" / "telegraph.json", token, httpx.AsyncClient(transport=httpx.MockTransport(api)))


async def test_account_is_created_once_and_token_file_is_private(tmp_path):
    api = TelegraphApi()
    client = Client(api, tmp_path)
    nodes = [{"tag": "p", "children": ["hi"]}]
    assert await client.CreatePage("Title", "Author", "https://a.example", nodes, URL) == "https://telegra.ph/Council-11-09"
    await client.CreatePage("Title 2", "Author", "https://a.example", nodes, URL)
    assert [m for m, _ in api.calls] == ["createAccount", "createPage", "createPage"]
    path = tmp_path / "data" / "telegraph.json"
    assert json.loads(path.read_text())["access_token"] == "TOKEN-1" and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # a new client (new process) reuses the stored token
    api2 = TelegraphApi()
    await Client(api2, tmp_path).CreatePage("T", "A", "", nodes, URL)
    assert [m for m, _ in api2.calls] == ["createPage"] and api2.calls[0][1]["access_token"] == "TOKEN-1"


async def test_explicit_token_is_used_and_no_account_created(tmp_path):
    api = TelegraphApi()
    await Client(api, tmp_path, token="MINE").CreatePage("T", "A", "", [{"tag": "p", "children": ["x"]}], URL)
    assert [m for m, _ in api.calls] == ["createPage"] and api.calls[0][1]["access_token"] == "MINE"
    assert not (tmp_path / "data" / "telegraph.json").exists()


async def test_page_payload_limits_and_validation(tmp_path):
    api = TelegraphApi()
    await Client(api, tmp_path, token="T").CreatePage("t" * 400, "a" * 300, "not a url", [{"tag": "p", "children": ["x"]}], URL)
    payload = api.calls[0][1]
    assert len(payload["title"]) == 256 and len(payload["author_name"]) == 128 and payload["author_url"] == ""
    assert payload["return_content"] is False


async def test_oversized_articles_are_trimmed_with_a_source_link(tmp_path):
    api = TelegraphApi()
    big = [{"tag": "p", "children": ["x" * 1000]} for _ in range(100)]
    await Client(api, tmp_path, token="T").CreatePage("T", "A", "", big, URL)
    content = api.calls[0][1]["content"]
    assert len(json.dumps(content).encode()) <= CONTENT_LIMIT_BYTES + 400
    assert content[-1]["children"][0]["attrs"]["href"] == URL


async def test_telegraph_errors_become_web_errors(tmp_path):
    with pytest.raises(WebError, match="CONTENT_TOO_BIG"):
        await Client(TelegraphApi("CONTENT_TOO_BIG"), tmp_path, token="T").CreatePage("T", "A", "", [{"tag": "p", "children": ["x"]}], URL)

    def Down(request):
        raise httpx.ConnectError("down")

    client = TelegraphClient(tmp_path / "t.json", "T", httpx.AsyncClient(transport=httpx.MockTransport(Down)))
    with pytest.raises(WebError, match="not reachable"):
        await client.CreatePage("T", "A", "", [{"tag": "p", "children": ["x"]}], URL)


async def test_new_page_is_checked_before_it_is_announced(tmp_path, monkeypatch):
    from minionbot.web import telegraph as telegraphModule

    monkeypatch.setattr(telegraphModule, "READABLE_DELAY", 0)
    api = TelegraphApi()
    api.pageStatuses = [404, 502, 200]  # not served yet, then fine
    assert await Client(api, tmp_path, token="T").CreatePage("T", "A", "", [{"tag": "p", "children": ["x"]}], URL)
    assert api.pageChecks == 3


async def test_a_page_that_never_becomes_readable_does_not_break_the_reply(tmp_path, monkeypatch, caplog):
    from minionbot.web import telegraph as telegraphModule

    monkeypatch.setattr(telegraphModule, "READABLE_DELAY", 0)
    api = TelegraphApi()
    api.pageStatuses = [500] * 10
    assert await Client(api, tmp_path, token="T").CreatePage("T", "A", "", [{"tag": "p", "children": ["x"]}], URL) == "https://telegra.ph/Council-11-09"
    assert api.pageChecks == telegraphModule.READABLE_CHECKS and "not readable yet" in caplog.text


# ---- archive providers ----------------------------------------------------------------------------------

async def test_wayback_returns_the_closest_snapshot_over_https():
    def Handler(request):
        assert request.url.params["url"] == URL
        return httpx.Response(200, json={"archived_snapshots": {"closest": {"available": True, "url": "http://web.archive.org/web/2026/" + URL}}})

    assert await Wayback(httpx.AsyncClient(transport=httpx.MockTransport(Handler))).Find(URL) == "https://web.archive.org/web/2026/" + URL


@pytest.mark.parametrize("response", [httpx.Response(200, json={"archived_snapshots": {}}), httpx.Response(500, text="oops"), httpx.Response(200, json={"archived_snapshots": {"closest": {"available": False}}})])
async def test_wayback_without_a_snapshot(response):
    assert await Wayback(httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))).Find(URL) is None


# ---- the service -------------------------------------------------------------------------------------------

async def Resolve(host, port):
    return ["93.184.216.34"]


class FakeTelegraph:
    def __init__(self, fail=False):
        self.fail, self.pages = fail, []

    async def CreatePage(self, title, author, authorUrl, nodes, sourceUrl):
        if self.fail:
            raise WebError("Telegraph is not reachable.")
        self.pages.append((title, author, nodes, sourceUrl))
        return "https://telegra.ph/Council-11-09"

    async def Close(self):
        pass


class FakeArchive:
    def __init__(self, name, address):
        self.name, self.address, self.asked = name, address, []

    async def Find(self, url):
        self.asked.append(url)
        return self.address


def Service(pages, telegraph=None, archives=()):
    def Handler(request):
        body = pages.get(str(request.url))
        if body is None:
            return httpx.Response(404, text="not found", headers={"content-type": "text/html"})
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    fetcher = PageFetcher(httpx.AsyncClient(transport=httpx.MockTransport(Handler), follow_redirects=False), Resolve)
    return WebService(fetcher, telegraph if telegraph is not None else FakeTelegraph(), list(archives))


async def test_reads_an_article_and_publishes_it():
    telegraph = FakeTelegraph()
    result = await Service({URL: Article()}, telegraph).Read(URL)
    assert result.title == "Council approves riverfront plan" and result.siteName == "Example News"
    assert result.telegraphUrl == "https://telegra.ph/Council-11-09" and result.date.startswith("2026-11-09")
    assert result.summary.startswith("After a decade") and result.notes == []
    title, author, nodes, source = telegraph.pages[0]
    assert author == "Jane Reporter" and source == URL
    assert "Source: " in json.dumps(nodes) and "BUY CHEAP WATCHES" not in json.dumps(nodes)


async def test_summary_falls_back_to_the_first_paragraph_and_is_shortened():
    html = Article().replace('<meta name="description" content="After a decade of debate the council backed the riverfront plan.">', "")
    result = await Service({URL: html}).Read(URL)
    assert result.summary == PARAGRAPHS[0]  # the first real paragraph, not the repeated title heading


async def test_long_summaries_are_shortened_at_a_word_boundary():
    long = "word " * 200
    result = await Service({URL: Article(paragraphs=[long.strip()] + PARAGRAPHS).replace('<meta name="description" content="After a decade of debate the council backed the riverfront plan.">', "")}).Read(URL)
    assert len(result.summary) <= 350 and result.summary.endswith("…") and not result.summary.endswith(" …")


async def test_telegraph_failure_falls_back_to_text():
    result = await Service({URL: Article()}, FakeTelegraph(fail=True)).Read(URL)
    assert result.telegraphUrl is None and PARAGRAPHS[1][:50] in result.text


async def test_works_without_telegraph():
    result = await WebService(Service({URL: Article()}).fetcher, None).Read(URL)
    assert result.telegraphUrl is None and result.title


async def test_teaser_is_replaced_by_the_archived_copy():
    archive = FakeArchive("first-archive", "https://archive.example/newest/" + URL)
    service = Service({URL: TEASER, "https://archive.example/newest/" + URL: Article(paragraphs=PARAGRAPHS * 2)}, archives=[archive])
    result = await service.Read(URL)
    assert archive.asked == [URL] and len(result.text) > 1200
    assert result.notes == ["Only a teaser was served, so this is the archived copy from first-archive."]


async def test_archives_are_tried_in_order_until_one_has_the_full_text():
    first, second = FakeArchive("first-archive", "https://archive.example/newest/x"), FakeArchive("web.archive.org", "https://web.archive.org/web/1/x")
    service = Service({URL: TEASER, "https://web.archive.org/web/1/x": Article(paragraphs=PARAGRAPHS * 2)}, archives=[first, second])
    result = await service.Read(URL)  # first archive answers 404, second works
    assert first.asked == [URL] and second.asked == [URL] and "web.archive.org" in result.notes[0]


async def test_teaser_without_archive_copy_is_reported_honestly():
    result = await Service({URL: TEASER}, archives=[FakeArchive("first-archive", None)]).Read(URL)
    assert result.notes == ["Only a teaser was available; the full text is behind a paywall."]
    assert "Subscribe to continue" not in result.text or len(result.text) < 1200


async def test_archive_with_no_more_text_than_the_teaser_is_not_used():
    archive = FakeArchive("first-archive", "https://archive.example/newest/" + URL)
    result = await Service({URL: TEASER, "https://archive.example/newest/" + URL: TEASER}, archives=[archive]).Read(URL)
    assert "teaser was available" in result.notes[0]


async def test_archives_are_not_contacted_for_normal_articles():
    archive = FakeArchive("first-archive", "https://archive.example/newest/x")
    await Service({URL: Article()}, archives=[archive]).Read(URL)
    assert archive.asked == []


async def test_unreadable_page():
    with pytest.raises(WebError, match="readable content") as error:
        await Service({URL: "<html><body><nav>menu</nav></body></html>"}).Read(URL)
    assert str(error.value) == NOTHING_READABLE


async def test_fetch_errors_pass_through():
    with pytest.raises(WebError, match="HTTP 404"):
        await Service({}).Read(URL)


async def test_unexpected_bugs_become_unexpected_web_errors():
    class Boom:
        async def Fetch(self, url):
            raise ValueError("bug")

        async def Close(self):
            pass

    with pytest.raises(UnexpectedWebError) as error:
        await WebService(Boom(), None).Read(URL)
    assert isinstance(error.value.__cause__, ValueError)


# ---- refused pages (403, bot protection) -------------------------------------------------------------------

def RefusingService(archivePages, archives, status=403, **headers):
    def Handler(request):
        url = str(request.url)
        if url == URL:
            return httpx.Response(status, text="Forbidden", headers={"content-type": "text/html", **headers})
        body = archivePages.get(url)
        return httpx.Response(200 if body else 404, text=body or "nope", headers={"content-type": "text/html"})

    fetcher = PageFetcher(httpx.AsyncClient(transport=httpx.MockTransport(Handler), follow_redirects=False), Resolve)
    return WebService(fetcher, FakeTelegraph(), list(archives))


async def test_refused_page_is_read_from_the_archive():
    archive = FakeArchive("web.archive.org", "https://web.archive.org/web/1/x")
    service = RefusingService({"https://web.archive.org/web/1/x": Article()}, [archive], server="DataDome")
    result = await service.Read(URL)
    assert archive.asked == [URL] and result.title == "Council approves riverfront plan"
    assert result.notes == ["The site refused access, so this is the archived copy from web.archive.org."]
    assert result.sourceUrl == URL  # the Telegraph page points at the real article, not at the archive


async def test_a_short_archived_copy_is_fine_when_the_site_refused():
    archive = FakeArchive("web.archive.org", "https://web.archive.org/web/1/x")
    short = Article(paragraphs=[PARAGRAPHS[0], PARAGRAPHS[1]])
    result = await RefusingService({"https://web.archive.org/web/1/x": short}, [archive]).Read(URL)
    assert len(result.text) < 1200 and "archived copy" in result.notes[0]


async def test_refused_page_without_any_archived_copy_explains_both():
    with pytest.raises(WebError) as error:
        await RefusingService({}, [FakeArchive("first-archive", None), FakeArchive("web.archive.org", "https://web.archive.org/none")], server="DataDome").Read(URL)
    assert str(error.value) == "That site blocks automated access (bot protection), so I can't read it directly. I couldn't find an archived copy either."


async def test_plain_403_message_when_the_archive_has_nothing():
    with pytest.raises(WebError, match=r"HTTP 403\). I couldn't find an archived copy either"):
        await RefusingService({}, [FakeArchive("first-archive", None)]).Read(URL)


async def test_refusal_without_archives_configured_keeps_the_plain_message():
    with pytest.raises(WebError) as error:
        await RefusingService({}, []).Read(URL)
    assert str(error.value) == "The site answered with an error (HTTP 403)."


@pytest.mark.parametrize("status", [404, 410, 500])
async def test_missing_pages_do_not_contact_the_archive(status):
    archive = FakeArchive("first-archive", "https://archive.example/newest/x")
    with pytest.raises(WebError, match=f"HTTP {status}"):
        await RefusingService({}, [archive], status=status).Read(URL)
    assert archive.asked == []

import logging

import httpx
import pytest

from minionbot.search import serpapi
from minionbot.search.models import SearchError, SearchServiceError, UnexpectedSearchError
from minionbot.search.serpapi import MAX_ORGANIC, MAX_SOURCES, ParseAiOverview, ParseOrganic, SerpApiClient

from tests.search_fixtures import AI_OVERVIEW, Organic, Response

KEY = "SECRET-KEY-123"


def Client(handler, **kwargs):
    return SerpApiClient(KEY, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kwargs)


def Json(data, status=200):
    return httpx.Response(status, json=data)


# ---- parsing -------------------------------------------------------------------------------------------

def test_organic_results_are_limited_cleaned_and_validated():
    items = Organic(10)
    items[0]["snippet"] = "  lots   of\nspace  " + "x" * 600
    items.insert(0, {"title": "No link"})
    items.insert(0, {"title": "Bad link", "link": "javascript:alert(1)"})
    items.insert(0, "garbage")
    results = ParseOrganic({"organic_results": items})
    assert len(results) == MAX_ORGANIC and results[0].title == "Result 1 - Example"
    assert results[0].snippet.startswith("lots of space") and len(results[0].snippet) <= 400
    assert ParseOrganic({}) == [] and ParseOrganic({"organic_results": None}) == []


def test_ai_overview_blocks_and_sources():
    ai = ParseAiOverview(AI_OVERVIEW)
    assert [(b.kind, b.title, b.text) for b in ai.blocks] == [
        ("h", "", "What is Python?"),
        ("p", "", "Python is a high-level programming language."),
        ("li", "Readable", "The syntax is close to plain English."),
        ("li", "", "No lead-in here."),
    ]
    assert [(s.title, s.link) for s in ai.sources] == [("Python.org", "https://www.python.org/"), ("Docs", "https://docs.python.org/3/")]


def test_nested_blocks_sources_limit_and_bad_input():
    nested = {"text_blocks": [{"type": "expandable", "title": "More", "text_blocks": [{"type": "paragraph", "snippet": "inner"}]}],
              "references": [{"title": f"T{i}", "link": f"https://s{i}.example/"} for i in range(10)] + [{"title": "x", "link": "ftp://nope"}]}
    ai = ParseAiOverview(nested)
    assert [(b.kind, b.text) for b in ai.blocks] == [("h", "More"), ("p", "inner")] and len(ai.sources) == MAX_SOURCES
    for bad in (None, "text", [], {}, {"text_blocks": []}, {"text_blocks": [{"type": "table"}]}, {"page_token": "t"}):
        assert ParseAiOverview(bad) is None


def test_deeply_nested_blocks_are_cut_off():
    block = {"type": "paragraph", "snippet": "deep"}
    for _ in range(10):
        block = {"type": "expandable", "text_blocks": [block]}
    assert ParseAiOverview({"text_blocks": [block]}) is None


# ---- searching ---------------------------------------------------------------------------------------------

async def test_sends_the_documented_parameters():
    seen = []

    def Handler(request):
        seen.append(dict(request.url.params))
        return Json(Response())

    await Client(Handler, language="en", country="us").Search("  python   asyncio  ")
    assert seen == [{"engine": "google", "q": "python asyncio", "hl": "en", "gl": "us", "api_key": KEY}]


async def test_country_is_optional_and_query_is_capped():
    seen = []
    await Client(lambda request: (seen.append(dict(request.url.params)), Json(Response()))[1]).Search("x" * 1000)
    assert "gl" not in seen[0] and len(seen[0]["q"]) == serpapi.MAX_QUERY_CHARS


async def test_inline_ai_overview_needs_one_request():
    calls = []
    result = await Client(lambda request: (calls.append(1), Json(Response(ai=AI_OVERVIEW)))[1]).Search("q")
    assert len(calls) == 1 and result.ai.blocks and len(result.organic) == 3 and result.query == "q"


async def test_page_token_triggers_the_second_request():
    seen = []

    def Handler(request):
        params = dict(request.url.params)
        seen.append(params)
        if params["engine"] == "google":
            return Json(Response(aiToken="TOKEN-1"))
        return Json({"ai_overview": AI_OVERVIEW})

    result = await Client(Handler).Search("q")
    assert [p["engine"] for p in seen] == ["google", "google_ai_overview"]
    assert seen[1]["page_token"] == "TOKEN-1" and seen[1]["api_key"] == KEY and "q" not in seen[1]
    assert result.ai is not None and len(result.organic) == 3


async def test_second_response_without_the_wrapper_key_is_understood_too():
    handler = lambda request: Json(Response(aiToken="T")) if request.url.params["engine"] == "google" else Json(AI_OVERVIEW)
    assert (await Client(handler).Search("q")).ai is not None


@pytest.mark.parametrize("second", [
    lambda: httpx.Response(500, text="oops"),
    lambda: Json({"error": "Invalid API key"}),
    lambda: Json({"error": "Something else"}),
    lambda: Json({"unrelated": 1}),
])
async def test_a_failing_second_request_only_loses_the_ai_answer(second):
    def Handler(request):
        return Json(Response(aiToken="T")) if request.url.params["engine"] == "google" else second()

    result = await Client(Handler).Search("q")
    assert result.ai is None and len(result.organic) == 3


async def test_second_request_timeout_is_not_fatal():
    def Handler(request):
        if request.url.params["engine"] == "google":
            return Json(Response(aiToken="T"))
        raise httpx.ReadTimeout("slow")

    assert (await Client(Handler).Search("q")).ai is None


async def test_no_ai_overview_means_a_single_request():
    calls = []
    result = await Client(lambda request: (calls.append(1), Json(Response()))[1]).Search("q")
    assert result.ai is None and len(calls) == 1


async def test_empty_results_are_not_an_error():
    result = await Client(lambda request: Json({"error": "Google hasn't returned any results for this query."})).Search("zzzz")
    assert result.organic == [] and result.ai is None


# ---- errors -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("response", [
    httpx.Response(401, json={"error": "Invalid API key"}),
    httpx.Response(403, text="forbidden"),
    httpx.Response(200, json={"error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"}),
])
async def test_key_problems_are_service_errors(response):
    with pytest.raises(SearchServiceError, match="API key"):
        await Client(lambda request: response).Search("q")


@pytest.mark.parametrize("response", [
    httpx.Response(429, json={"error": "Your account has run out of searches."}),
    httpx.Response(200, json={"error": "Your account has run out of searches."}),
])
async def test_quota_problems_are_service_errors(response):
    with pytest.raises(SearchServiceError, match="quota"):
        await Client(lambda request: response).Search("q")


@pytest.mark.parametrize("response", [httpx.Response(500, text="boom"), httpx.Response(502, text="<html>bad gateway</html>"), httpx.Response(200, json={"error": "Weird failure"})])
async def test_other_failures_are_plain_search_errors(response):
    with pytest.raises(SearchError) as error:
        await Client(lambda request: response).Search("q")
    assert not isinstance(error.value, SearchServiceError) and "error" in str(error.value)


async def test_network_failures():
    def Timeout(request):
        raise httpx.ReadTimeout("slow")

    def Down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(SearchError, match="too long"):
        await Client(Timeout).Search("q")
    with pytest.raises(SearchError, match="not reachable"):
        await Client(Down).Search("q")


async def test_bugs_become_unexpected_search_errors():
    with pytest.raises(UnexpectedSearchError) as error:
        await Client(lambda request: Json(Response(organic="not a number"))).Search("q")
    assert isinstance(error.value.__cause__, Exception)


async def test_the_api_key_never_reaches_messages_or_logs(caplog):
    """httpx errors carry the full URL, including api_key=..., so none of that may leak."""
    def Leaky(request):
        raise httpx.ConnectError(f"failed to reach {request.url}")

    caplog.set_level(logging.DEBUG, logger="minionbot")  # httpx is kept at WARNING by the bot's logging setup
    for handler in (Leaky, lambda request: httpx.Response(500, text=f"error for {request.url}"), lambda request: Json({"error": f"bad {request.url}"})):
        with pytest.raises(SearchError) as error:
            await Client(handler).Search("q")
        assert KEY not in str(error.value)
    assert KEY not in caplog.text


async def test_close():
    client = Client(lambda request: Json(Response()))
    await client.Close()

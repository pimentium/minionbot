import httpx
import pytest

from minionbot.web import fetcher as fetcherModule
from minionbot.web.errors import UnsafeUrlError, WebError
from minionbot.web.fetcher import PageFetcher

HOSTS = {"example.com": ["93.184.216.34"], "other.example": ["93.184.216.35"], "internal.example": ["10.0.0.9"]}


async def Resolve(host, port):
    if host not in HOSTS:
        raise OSError("unknown")
    return HOSTS[host]


def Fetcher(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return PageFetcher(client, Resolve)


def Html(text="<html><body>hello</body></html>", status=200, contentType="text/html; charset=utf-8", **headers):
    return httpx.Response(status, text=text, headers={"content-type": contentType, **headers})


async def test_fetches_a_page():
    page = await Fetcher(lambda request: Html()).Fetch("https://example.com/a")
    assert "hello" in page.html and page.finalUrl == "https://example.com/a" and page.url == "https://example.com/a"


async def test_redirects_are_followed_and_final_url_reported():
    def Handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://other.example/final"})
        return Html("<html><body>arrived</body></html>")

    page = await Fetcher(Handler).Fetch("https://example.com/start")
    assert page.finalUrl == "https://other.example/final" and "arrived" in page.html


async def test_relative_redirect():
    def Handler(request):
        return httpx.Response(301, headers={"location": "/moved"}) if request.url.path == "/old" else Html()

    assert (await Fetcher(Handler).Fetch("https://example.com/old")).finalUrl == "https://example.com/moved"


async def test_redirect_to_a_private_address_is_blocked_before_it_is_requested():
    requested = []

    def Handler(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://internal.example/admin"})

    with pytest.raises(UnsafeUrlError):
        await Fetcher(Handler).Fetch("https://example.com/redirector")
    assert requested == ["https://example.com/redirector"]


async def test_redirect_to_metadata_ip_literal_is_blocked():
    handler = lambda request: httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    with pytest.raises(UnsafeUrlError):
        await Fetcher(handler).Fetch("https://example.com/")


async def test_first_address_private_is_blocked_without_any_request():
    called = []
    with pytest.raises(UnsafeUrlError):
        await Fetcher(lambda request: called.append(1) or Html()).Fetch("https://internal.example/")
    assert called == []


async def test_redirect_loop_gives_up():
    handler = lambda request: httpx.Response(302, headers={"location": "https://example.com/again"})
    with pytest.raises(WebError, match="redirects"):
        await Fetcher(handler).Fetch("https://example.com/")


async def test_broken_redirect():
    with pytest.raises(WebError, match="redirect"):
        await Fetcher(lambda request: httpx.Response(302)).Fetch("https://example.com/")


@pytest.mark.parametrize("contentType", ["image/jpeg", "application/pdf", "video/mp4", "application/json"])
async def test_only_web_pages_are_accepted(contentType):
    with pytest.raises(WebError, match="not a web page"):
        await Fetcher(lambda request: Html("x", contentType=contentType)).Fetch("https://example.com/file")


async def test_plain_text_is_accepted():
    page = await Fetcher(lambda request: Html("just text", contentType="text/plain")).Fetch("https://example.com/t.txt")
    assert page.html == "just text"


@pytest.mark.parametrize("status", [403, 404, 410, 500])
async def test_http_errors(status):
    with pytest.raises(WebError, match=f"HTTP {status}"):
        await Fetcher(lambda request: Html("nope", status=status)).Fetch("https://example.com/")


async def test_large_pages_are_refused_by_header_and_while_streaming(monkeypatch):
    monkeypatch.setattr(fetcherModule, "MAX_BYTES", 100)
    with pytest.raises(WebError, match="too large"):
        await Fetcher(lambda request: Html("x" * 10, **{"content-length": "5000"})).Fetch("https://example.com/")
    with pytest.raises(WebError, match="too large"):
        await Fetcher(lambda request: Html("x" * 500)).Fetch("https://example.com/")


async def test_timeouts_and_network_errors():
    def Timeout(request):
        raise httpx.ReadTimeout("slow")

    def Broken(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(WebError, match="too long"):
        await Fetcher(Timeout).Fetch("https://example.com/")
    with pytest.raises(WebError, match="couldn't open"):
        await Fetcher(Broken).Fetch("https://example.com/")


async def test_peer_address_is_checked_after_connecting():
    """Defence against DNS rebinding: the name looked public, but the connection went to a private address."""

    class Stream:
        def __init__(self, address):
            self.address = address

        def get_extra_info(self, name):
            return (self.address, 443) if name == "server_addr" else None

    def Handler(address):
        return lambda request: httpx.Response(200, text="secret", headers={"content-type": "text/html"}, extensions={"network_stream": Stream(address)})

    with pytest.raises(UnsafeUrlError):
        await Fetcher(Handler("10.0.0.7")).Fetch("https://example.com/")
    assert (await Fetcher(Handler("93.184.216.34")).Fetch("https://example.com/")).html == "secret"


async def test_default_headers_look_like_a_browser_without_zstd():
    assert "Firefox" in fetcherModule.PAGE_HEADERS["User-Agent"] and "zstd" not in fetcherModule.PAGE_HEADERS["Accept-Encoding"]
    assert PageFetcher().client.follow_redirects is False  # redirects are followed (and checked) by hand


# ---- refusals and bot protection ------------------------------------------------------------------------

from minionbot.web.errors import HttpStatusError


def Refusal(status=403, text="Access denied", **headers):
    return lambda request: httpx.Response(status, text=text, headers={"content-type": "text/html", **headers})


async def Error(handler):
    with pytest.raises(HttpStatusError) as caught:
        await Fetcher(handler).Fetch("https://example.com/")
    return caught.value


async def test_datadome_wall_is_recognised_by_its_server_header():
    error = await Error(Refusal(403, "<html><title>nytimes.com</title></html>", server="DataDome"))
    assert error.botProtection and error.refused and error.status == 403
    assert "blocks automated access" in str(error) and "403" not in str(error)


@pytest.mark.parametrize("status,text,headers", [
    (403, "<title>Just a moment...</title>", {}),
    (503, "<html>cf-chl-bypass</html>", {}),
    (403, "x", {"server": "cloudflare"}),
    (429, "Our systems have detected unusual traffic", {}),
    (403, "x", {"cf-mitigated": "challenge"}),
])
async def test_other_bot_protection_pages(status, text, headers):
    assert (await Error(Refusal(status, text, **headers))).botProtection


@pytest.mark.parametrize("status", [401, 402, 403, 429, 451])
async def test_refusal_statuses_are_worth_an_archive_lookup(status):
    error = await Error(Refusal(status, "plain refusal"))
    assert error.refused and not error.botProtection and f"HTTP {status}" in str(error)


@pytest.mark.parametrize("status", [404, 410, 500, 502])
async def test_missing_pages_and_server_errors_are_not_refusals(status):
    error = await Error(Refusal(status, "gone"))
    assert not error.refused and f"HTTP {status}" in str(error)


async def test_a_404_that_mentions_cloudflare_is_not_bot_protection():
    assert not (await Error(Refusal(404, "cloudflare cdn page not found"))).botProtection


def test_connecting_gives_up_after_eight_seconds():
    assert PageFetcher().client.timeout.connect == 8.0

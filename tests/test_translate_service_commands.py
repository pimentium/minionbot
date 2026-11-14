"""/rus and /eng: the /url pipeline with a translation step."""

import json
from unittest.mock import MagicMock

import httpx
import pytest
from telegram.constants import ChatType
from telegram.ext import CommandHandler

from minionbot import main
from minionbot.bot import BuildApplication, MakeUrlCommandCallback
from minionbot.builtin import ENG_USAGE, RUS_USAGE
from minionbot.config import Config, LoadConfig
from minionbot.translate.gemini import DEFAULT_MODEL, GeminiClient
from minionbot.users import Role, UserRef
from minionbot.web.errors import TranslationError, TranslationServiceError, WebError
from minionbot.web.fetcher import PageFetcher
from minionbot.web.service import WebService

from tests.conftest import OWNER_ID, STRANGER_ID
from tests.test_translate_articles import Fake
from tests.test_translate_language import ENGLISH, RUSSIAN
from tests.test_url_command import FakeWeb, PAGE, Result, RunUrl, Texts
from tests.test_bot import FakeContext, FakeMessage, FakeNotifier, FakeService, FakeUpdate
from tests.test_web_telegraph_archive_service import FakeTelegraph, Resolve
from tests.web_fixtures import PARAGRAPHS, Article

URL = "https://news.example.com/riverfront"


def RussianParagraphs():
    return [RUSSIAN + f" Подробность номер {number}, о которой много говорили в городе." for number in range(6)]  # distinct: the extractor drops duplicates


def Service(html, translator, telegraph=None):
    def Handler(request):
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    fetcher = PageFetcher(httpx.AsyncClient(transport=httpx.MockTransport(Handler), follow_redirects=False), Resolve)
    return WebService(fetcher, telegraph if telegraph is not None else FakeTelegraph(), [], translator)


# ---- WebService -----------------------------------------------------------------------------------------------

async def test_translated_page_is_published_with_title_text_and_a_machine_translation_footer():
    telegraph, translator = FakeTelegraph(), Fake()
    result = await Service(Article(), translator, telegraph).Read(URL, "ru")
    title, author, nodes, source = telegraph.pages[0]
    assert title == "RU[Council approves riverfront plan]" and source == URL
    dump = json.dumps(nodes, ensure_ascii=False)
    assert "RU[" in dump and "Machine translation to Russian (Gemini)" in dump and "Source: " in dump
    assert translator.calls[0][1] == "ru" and result.telegraphUrl == "https://telegra.ph/Council-11-09" and result.notes == []


async def test_english_target_and_fallback_text_are_translated_too():
    russian = Article(paragraphs=RussianParagraphs())
    result = await Service(russian, Fake(), telegraph=None).Read(URL, "en")
    assert result.text.count("RU[") > 3  # the plain-text fallback carries the translation
    assert "Machine translation" not in result.text and result.notes == []


async def test_without_a_language_nothing_is_translated():
    translator = Fake()
    result = await Service(Article(), translator).Read(URL)
    assert translator.calls == [] and "RU[" not in result.text


async def test_a_page_already_in_the_target_language_is_not_sent_to_gemini():
    translator = Fake()
    page = Article(paragraphs=[ENGLISH, ENGLISH, ENGLISH])
    result = await Service(page, translator).Read(URL, "en")
    assert translator.calls == [] and result.notes == ["The page is already in English, so it was not translated."]
    russian = Article(paragraphs=RussianParagraphs())
    result = await Service(russian, translator).Read(URL, "ru")
    assert translator.calls == [] and "already in Russian" in result.notes[0]


async def test_translation_needs_a_translator_and_fails_before_any_fetch():
    class Boom:
        async def Fetch(self, url):
            raise AssertionError("must not fetch")

        async def Close(self):
            pass

    with pytest.raises(WebError, match="not configured"):
        await WebService(Boom(), None).Read(URL, "ru")


async def test_translation_errors_reach_the_caller_and_publish_nothing():
    telegraph = FakeTelegraph()
    with pytest.raises(TranslationError, match="busy"):
        await Service(Article(), Fake(error=TranslationError("busy")), telegraph).Read(URL, "ru")
    assert telegraph.pages == []


async def test_a_cut_long_page_says_so_in_the_chat_note(monkeypatch):
    from minionbot.translate import articles

    monkeypatch.setattr(articles, "MAX_TRANSLATE_CHARS", 300)
    result = await Service(Article(paragraphs=PARAGRAPHS * 2), Fake()).Read(URL, "ru")
    assert result.notes == ["Only the first part of this long page was translated."]


async def test_service_close_closes_the_translator():
    closed = []

    class Closable(Fake):
        async def Close(self):
            closed.append(1)

    await Service(Article(), Closable()).Close()
    assert closed == [1]


# ---- the commands --------------------------------------------------------------------------------------------------

async def RunTranslate(registry, store, name, text, web, userId=OWNER_ID, chatType=ChatType.PRIVATE, notifier=None):
    language, usage = {"rus": ("ru", RUS_USAGE), "eng": ("en", ENG_USAGE)}[name]
    message = FakeMessage(text)
    await MakeUrlCommandCallback(registry, store, web, notifier, name, language, usage)(FakeUpdate(message, userId, chatType=chatType), FakeContext([]))
    return message


@pytest.mark.parametrize("name,language", [("rus", "ru"), ("eng", "en")])
async def test_each_command_asks_for_its_language(registry, store, name, language):
    web = FakeWeb()
    message = await RunTranslate(registry, store, name, f"/{name} {PAGE}?utm_source=x", web)
    assert web.read == [PAGE] and web.languages == [language] and Texts(message)[0].endswith("telegra.ph/Story-11-09")


@pytest.mark.parametrize("name,usage", [("rus", RUS_USAGE), ("eng", ENG_USAGE)])
async def test_usage_when_there_is_no_web_link(registry, store, name, usage):
    web = FakeWeb()
    message = await RunTranslate(registry, store, name, f"/{name} hello", web)
    message.reply_text.assert_awaited_once_with(usage)
    assert web.read == []


async def test_url_itself_does_not_translate(registry, store):
    web = FakeWeb()
    await RunUrl(registry, store, f"/url {PAGE}", web)
    assert web.languages == [None]


async def test_not_configured_says_so_without_fetching(registry, store):
    web = FakeWeb(translator=None)
    message = await RunTranslate(registry, store, "rus", f"/rus {PAGE}", web)
    assert "Gemini API key" in Texts(message)[0] and web.read == []
    message = await RunTranslate(registry, store, "eng", f"/eng {PAGE}", None)
    assert "Gemini API key" in Texts(message)[0]


async def test_social_links_are_pointed_to_social(registry, store):
    web = FakeWeb()
    message = await RunTranslate(registry, store, "rus", "/rus https://x.com/jack/status/20", web)
    assert "/social" in Texts(message)[0] and web.read == []


async def test_strangers_are_denied_and_users_work_in_groups(registry, store):
    web = FakeWeb()
    message = await RunTranslate(registry, store, "eng", f"/eng {PAGE}", web, userId=STRANGER_ID)
    message.reply_text.assert_awaited_once_with(store.DeniedText())
    assert web.read == []
    store.Add(UserRef(telegramId=55), role=Role.USER)
    await RunTranslate(registry, store, "rus", f"/rus {PAGE}", web, userId=55, chatType=ChatType.SUPERGROUP)
    assert web.read == [PAGE]


async def test_plain_translation_errors_are_shown_but_key_problems_notify_the_owner(registry, store):
    notifier = FakeNotifier()
    busy = FakeWeb(error=TranslationError("The translation service is busy (free-tier limit); try again in a minute."))
    message = await RunTranslate(registry, store, "rus", f"/rus {PAGE}", busy, notifier=notifier)
    assert Texts(message)[0].startswith("The translation service is busy") and PAGE in Texts(message)[0]
    notifier.ReportFailure.assert_not_awaited()
    error = TranslationServiceError("The translation service refused the API key.")
    await RunTranslate(registry, store, "rus", f"/rus {PAGE}", FakeWeb(error=error), notifier=notifier)
    assert notifier.ReportFailure.call_args[0][1] is error


def test_commands_are_registered_listed_and_open_to_groups(registry, store):
    app = BuildApplication(Config("123:FAKE", "1", "x"), store, registry, FakeService(), webService=FakeWeb())
    names = {name for h in app.handlers[0] if isinstance(h, CommandHandler) for name in h.commands}
    assert {"rus", "eng", "url"} <= names
    help = registry.Dispatch("help", [], OWNER_ID, None, store)
    assert "/rus <link>" in help and "/eng <link>" in help and "Russian" in help and "English" in help
    assert all(registry.commands[name].inGroups and registry.commands[name].needsChat for name in ("rus", "eng"))


# ---- configuration and start-up ---------------------------------------------------------------------------------------

def test_gemini_settings():
    env = {"BOT_TOKEN": "t", "INITIAL_USER": "1"}
    config = LoadConfig(env)
    assert (config.geminiKey, config.geminiModel) == ("", DEFAULT_MODEL)
    config = LoadConfig({**env, "GEMINI_API_KEY": " envkey ", "GEMINI_MODEL": "gemini-x"})
    assert (config.geminiKey, config.geminiModel) == ("envkey", "gemini-x")
    config = LoadConfig({**env, "GEMINI_API_KEY": "envkey"}, geminiKey="clikey", geminiModel="gemini-y")
    assert (config.geminiKey, config.geminiModel) == ("clikey", "gemini-y")


def test_the_translator_exists_only_with_a_key(monkeypatch, tmp_path):
    for name in ("BOT_TOKEN", "INITIAL_USER", "USERS_FILE", "GEMINI_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    monkeypatch.setattr(main, "MediaFetcher", MagicMock())
    monkeypatch.setattr(main, "BuildApplication", MagicMock(return_value=MagicMock()))
    webService = MagicMock()
    monkeypatch.setattr(main, "WebService", webService)
    base = ["--token", "1:T", "--initial-user", "42", "--users-file", str(tmp_path / "u.json")]
    main.Main(base)
    assert webService.call_args[0][3] is None
    main.Main(base + ["--gemini-key", "KEY", "--gemini-model", "gemini-z"])
    translator = webService.call_args[0][3]
    assert isinstance(translator, GeminiClient) and (translator.apiKey, translator.model) == ("KEY", "gemini-z")

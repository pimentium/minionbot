"""/rus and /eng without a link: translate the given text, or the message that was replied to."""

from unittest.mock import MagicMock

import pytest
from telegram.constants import ChatType

from minionbot.bot import CHUNK_CHARS, MakeUrlCommandCallback
from minionbot.builtin import ENG_USAGE, RUS_USAGE
from minionbot.translate.text import TranslateText
from minionbot.web.errors import TranslationError, TranslationServiceError

from tests.conftest import OWNER_ID, STRANGER_ID
from tests.test_bot import FakeContext, FakeMessage, FakeNotifier, FakeUpdate
from tests.test_translate_articles import Fake
from tests.test_translate_language import ENGLISH, RUSSIAN
from tests.test_url_command import FakeWeb, PAGE, Texts

ARGS = {"rus": ("ru", RUS_USAGE), "eng": ("en", ENG_USAGE)}


async def Run(registry, store, name, text, translator=None, replyTo=None, userId=OWNER_ID, notifier=None, quote=None, web=None, rawArgs=None):
    language, usage = ARGS[name]
    web = web or FakeWeb(translator=translator)
    message = FakeMessage(text, replyTo=replyTo)
    if quote is not None:
        message.quote = MagicMock(text=quote)
    context = FakeContext([])
    if rawArgs is not None:
        context.rawArgs = rawArgs
    await MakeUrlCommandCallback(registry, store, web, notifier, name, language, usage)(FakeUpdate(message, userId, chatType=ChatType.PRIVATE), context)
    return message, web


# ---- TranslateText ----------------------------------------------------------------------------------------------------

async def test_lines_are_translated_separately_and_blank_lines_kept():
    translator = Fake()
    result = await TranslateText("first line\n\nsecond line", translator, "ru")
    assert translator.calls == [(["first line", "second line"], "ru")] and result == "RU[first line]\n\nRU[second line]"


async def test_text_is_escaped_going_in_and_plain_text_coming_out():
    translator = Fake(transform=lambda s: f"<b>{s}</b><script>x</script>")
    result = await TranslateText("a < b & c > d", translator, "ru")
    assert translator.calls[0][0] == ["a &lt; b &amp; c &gt; d"]
    assert result == "a < b & c > dx"  # tags dropped, entities back to characters


async def test_a_wrong_number_of_fragments_or_nothing_to_translate_is_an_error():
    class Short:
        async def TranslateMany(self, strings, language):
            return []

    with pytest.raises(TranslationError, match="malformed"):
        await TranslateText("hello", Short(), "ru")
    with pytest.raises(TranslationError, match="nothing"):
        await TranslateText(" \n ", Fake(), "ru")


async def test_an_empty_answer_keeps_the_original_line():
    assert await TranslateText("keep me", Fake(transform=lambda s: ""), "ru") == "keep me"


# ---- the commands -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,language", [("rus", "ru"), ("eng", "en")])
async def test_text_after_the_command_is_translated(registry, store, name, language):
    translator = Fake()
    message, web = await Run(registry, store, name, f"/{name} hello world\nsecond", translator)
    assert translator.calls == [(["hello world", "second"], language)] and web.read == []
    assert Texts(message) == ["RU[hello world]\nRU[second]"]
    assert message.reply_text.call_args[1]["link_preview_options"].is_disabled


async def test_text_after_a_mention_uses_the_raw_arguments(registry, store):
    translator = Fake()
    message, _ = await Run(registry, store, "rus", "@Bot /rus ignored", translator, rawArgs="from the mention")
    assert translator.calls[0][0] == ["from the mention"] and Texts(message) == ["RU[from the mention]"]


async def test_a_reply_without_a_link_translates_the_replied_to_message(registry, store):
    translator = Fake()
    message, _ = await Run(registry, store, "rus", "/rus", translator, replyTo=FakeMessage("Good morning"))
    assert translator.calls == [(["Good morning"], "ru")] and Texts(message) == ["RU[Good morning]"]


async def test_a_replied_to_caption_is_translated_and_a_selected_quote_wins(registry, store):
    replied = FakeMessage(None)
    replied.caption = "photo caption"
    translator = Fake()
    await Run(registry, store, "rus", "/rus", translator, replyTo=replied)
    assert translator.calls[0][0] == ["photo caption"]
    translator = Fake()
    await Run(registry, store, "rus", "/rus", translator, replyTo=FakeMessage("whole"), quote="only this")
    assert translator.calls[0][0] == ["only this"]


async def test_own_text_wins_over_the_replied_to_message(registry, store):
    translator = Fake()
    await Run(registry, store, "eng", "/eng mine", translator, replyTo=FakeMessage("theirs"))
    assert translator.calls[0][0] == ["mine"]


async def test_a_link_still_means_reading_the_page(registry, store):
    translator, web = Fake(), FakeWeb()
    message, web = await Run(registry, store, "rus", f"/rus {PAGE}", translator, web=web)
    assert web.read == [PAGE] and translator.calls == []
    web = FakeWeb()
    await Run(registry, store, "rus", "/rus", translator, replyTo=FakeMessage(f"read {PAGE}"), web=web)
    assert web.read == [PAGE] and translator.calls == []


async def test_a_link_in_the_command_wins_over_text_around_it(registry, store):
    web = FakeWeb()
    await Run(registry, store, "rus", f"/rus please translate {PAGE} now", Fake(), web=web)
    assert web.read == [PAGE]


async def test_url_command_does_not_translate_text(registry, store):
    from minionbot.builtin import URL_USAGE

    web, message = FakeWeb(), FakeMessage("/url just words")
    await MakeUrlCommandCallback(registry, store, web)(FakeUpdate(message), FakeContext([]))
    message.reply_text.assert_awaited_once_with(URL_USAGE)


async def test_social_links_still_point_to_social(registry, store):
    message, _ = await Run(registry, store, "rus", "/rus https://x.com/alice/status/1", Fake())
    assert "/social" in Texts(message)[0]


async def test_text_already_in_the_target_language_is_not_translated(registry, store):
    translator = Fake()
    message, _ = await Run(registry, store, "eng", f"/eng {ENGLISH}", translator)
    assert translator.calls == [] and "already in English" in Texts(message)[0]
    message, _ = await Run(registry, store, "rus", f"/rus {RUSSIAN}", translator)
    assert translator.calls == [] and "already in Russian" in Texts(message)[0]


async def test_not_configured_without_a_translator(registry, store):
    message, _ = await Run(registry, store, "rus", "/rus hello", translator=None)
    assert "not configured" in Texts(message)[0]


async def test_a_long_translation_is_split_into_messages(registry, store):
    text = "\n".join(f"line {i} " + "word " * 20 for i in range(200))
    message, _ = await Run(registry, store, "rus", f"/rus {text}", Fake(transform=lambda s: s))
    assert message.reply_text.await_count > 1 and all(len(t) <= CHUNK_CHARS for t in Texts(message))


async def test_translation_errors_are_shown_and_service_errors_notify_the_owner(registry, store):
    notifier = FakeNotifier()
    message, _ = await Run(registry, store, "rus", "/rus hello", Fake(error=TranslationError("busy")), notifier=notifier)
    assert Texts(message) == ["busy"] and notifier.ReportFailure.await_count == 0
    message, _ = await Run(registry, store, "rus", "/rus hello", Fake(error=TranslationServiceError("bad key")), notifier=notifier)
    assert Texts(message) == ["bad key"] and notifier.ReportFailure.await_count == 1
    message, _ = await Run(registry, store, "rus", "/rus hello", Fake(error=RuntimeError("boom")), notifier=notifier)
    assert "went wrong" in Texts(message)[0] and notifier.ReportFailure.await_count == 2


async def test_strangers_get_nothing_translated(registry, store):
    translator = Fake()
    message, _ = await Run(registry, store, "rus", "/rus hello", translator, userId=STRANGER_ID)
    assert translator.calls == [] and "ask my owner" in Texts(message)[0]

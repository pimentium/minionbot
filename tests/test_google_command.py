"""Stage 3: /google."""

from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.ext import CommandHandler

from minionbot.bot import BuildApplication, MakeGoogleCommandCallback
from minionbot.builtin import GOOGLE_USAGE
from minionbot.config import Config
from minionbot.search.models import SearchError, SearchResult, SearchServiceError, UnexpectedSearchError
from minionbot.search.serpapi import MAX_QUERY_CHARS, ParseAiOverview, ParseOrganic
from minionbot.users import Role, UserRef

from tests.conftest import OWNER_ID, STRANGER_ID
from tests.search_fixtures import AI_OVERVIEW, Organic
from tests.test_bot import FakeContext, FakeMessage, FakeNotifier, FakeService, FakeUpdate


def Result(ai=True, organic=3, query="python asyncio"):
    return SearchResult(query, ParseOrganic({"organic_results": Organic(organic)}), ParseAiOverview(AI_OVERVIEW) if ai else None)


class FakeSearch:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.queries = result or Result(), error, []

    async def Search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.result


async def Run(registry, store, args, search, userId=OWNER_ID, chatType=ChatType.PRIVATE, notifier=None):
    message = FakeMessage("/google " + " ".join(args))
    update = FakeUpdate(message, userId, chatType=chatType)
    await MakeGoogleCommandCallback(registry, store, search, notifier)(update, FakeContext(args))
    return message, update


def Sent(message):
    return [call[0][0] for call in message.reply_text.call_args_list]


async def test_answers_with_the_ai_overview_first_then_the_results(registry, store):
    search = FakeSearch()
    message, update = await Run(registry, store, ["python", "asyncio"], search)
    assert search.queries == ["python asyncio"]
    texts = Sent(message)
    assert len(texts) == 2 and texts[0].startswith("<b>AI overview") and texts[1].startswith("<b>Google: python asyncio</b>")
    for call in message.reply_text.call_args_list:
        assert call[1]["parse_mode"] == ParseMode.HTML and call[1]["link_preview_options"].is_disabled is True
    update.effective_chat.send_action.assert_awaited_once_with(ChatAction.TYPING)


async def test_only_results_when_google_showed_no_ai_overview(registry, store):
    message, _ = await Run(registry, store, ["q"], FakeSearch(Result(ai=False)))
    assert len(Sent(message)) == 1 and Sent(message)[0].startswith("<b>Google: ")


async def test_no_results(registry, store):
    message, _ = await Run(registry, store, ["zzzz"], FakeSearch(Result(ai=False, organic=0, query="zzzz")))
    assert Sent(message) == ["No results for “zzzz”."]


@pytest.mark.parametrize("args", [[], [" "], ["", ""]])
async def test_usage_without_a_query(registry, store, args):
    search = FakeSearch()
    message, _ = await Run(registry, store, args, search)
    message.reply_text.assert_awaited_once_with(GOOGLE_USAGE)
    assert search.queries == []


async def test_overlong_queries_are_refused_before_any_request(registry, store):
    search = FakeSearch()
    message, _ = await Run(registry, store, ["x" * (MAX_QUERY_CHARS + 1)], search)
    assert "too long" in Sent(message)[0] and search.queries == []


async def test_not_configured(registry, store):
    message, _ = await Run(registry, store, ["q"], None)
    assert "not configured" in Sent(message)[0] and "SerpApi" in Sent(message)[0]


async def test_unknown_role_is_denied_and_no_search_is_made(registry, store):
    search = FakeSearch()
    message, _ = await Run(registry, store, ["q"], search, userId=STRANGER_ID)
    message.reply_text.assert_awaited_once_with(store.DeniedText())
    assert search.queries == []


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
async def test_user_and_admin_may_search_also_in_groups(registry, store, role):
    store.Add(UserRef(telegramId=66), role=role)
    search = FakeSearch()
    await Run(registry, store, ["hello"], search, userId=66, chatType=ChatType.SUPERGROUP)
    assert search.queries == ["hello"]


async def test_plain_search_errors_are_shown_but_do_not_notify(registry, store):
    notifier = FakeNotifier()
    message, _ = await Run(registry, store, ["q"], FakeSearch(error=SearchError("The search service took too long to answer.")), notifier=notifier)
    assert Sent(message) == ["The search service took too long to answer."]
    notifier.ReportFailure.assert_not_awaited()


async def test_key_and_quota_problems_notify_the_owner(registry, store):
    notifier = FakeNotifier()
    error = SearchServiceError("The search service refused the API key.")
    message, _ = await Run(registry, store, ["q"], FakeSearch(error=error), notifier=notifier)
    assert Sent(message) == ["The search service refused the API key."]
    assert notifier.ReportFailure.call_args[0][1] is error


async def test_unexpected_failures_notify_the_owner_with_the_cause(registry, store):
    cause = ValueError("bug")
    error = UnexpectedSearchError("Sorry, something went wrong while searching.")
    error.__cause__ = cause
    notifier = FakeNotifier()
    await Run(registry, store, ["q"], FakeSearch(error=error), notifier=notifier)
    assert notifier.ReportFailure.call_args[0][1] is cause


async def test_send_failures_notify_the_owner(registry, store):
    notifier = FakeNotifier()
    message = FakeMessage("/google q")
    message.reply_text = AsyncMock(side_effect=[RuntimeError("telegram rejected the html"), None])
    await MakeGoogleCommandCallback(registry, store, FakeSearch(), notifier)(FakeUpdate(message), FakeContext(["q"]))
    assert notifier.ReportFailure.call_args[0][1].args == ("telegram rejected the html",)
    assert "couldn't send" in Sent(message)[-1]


async def test_updates_without_a_message_are_ignored(registry, store):
    from unittest.mock import MagicMock

    update = MagicMock()
    update.effective_message = None
    await MakeGoogleCommandCallback(registry, store, FakeSearch())(update, FakeContext([]))


def test_google_is_registered_for_groups_and_listed(registry, store):
    app = BuildApplication(Config("123:FAKE", "1", "x"), store, registry, FakeService(), searchClient=FakeSearch())
    assert "google" in {name for h in app.handlers[0] if isinstance(h, CommandHandler) for name in h.commands}
    assert "/google <query>" in registry.Dispatch("help", [], OWNER_ID, None, store)

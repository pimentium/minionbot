"""/help: sections by role (least restricted first), command line + indented description (summary, then details)."""

import html
import re
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ParseMode
from telegram.error import BadRequest

from minionbot.bot import MakeCommandCallback, SplitHtmlBlocks
from minionbot.builtin import ROLE_TITLES, BuildRegistry
from minionbot.commands import HelpEntry
from minionbot.users import Role, UserRef

from tests.conftest import OWNER_ID
from tests.test_bot import FakeContext, FakeMessage, FakeUpdate
from tests.conftest import STRANGER_ID
from tests.test_roles import ADMIN_ID, USER_ID, Run, team  # noqa: F401 (team is a fixture)


def Titles(text):
    return re.findall(r"<b>([^<]+)</b>", text)


def test_owner_sees_every_section_least_restricted_first(registry, team):
    reply = Run(registry, team, "/help", OWNER_ID)
    assert Titles(reply) == ["Everyone", "User commands", "Admin commands", "Owner commands"]
    assert reply.index("/gemini") < reply.index("/users list") < reply.index("/restart")


def test_each_role_only_gets_its_own_and_lower_sections(registry, team):
    assert Titles(Run(registry, team, "/help", ADMIN_ID)) == ["Everyone", "User commands", "Admin commands"]
    assert Titles(Run(registry, team, "/help", USER_ID)) == ["Everyone", "User commands"]


def test_every_command_is_listed_under_its_own_role(registry, team):
    reply = Run(registry, team, "/help", OWNER_ID)
    sections = re.split(r"<b>[^<]+</b>", reply)[1:]
    everyone, user, admin, owner = sections
    assert "/whoami" in everyone and "/whoami" not in user
    assert "/restart" in owner and "/shutdown" in owner and "/users" not in owner and "/gemini" not in owner
    assert "/users add" in admin and "/restart" not in admin
    assert all(f"/{name}" in user for name in ("help", "social", "url", "rus", "eng", "gemini", "google"))


def test_the_command_is_on_its_own_line_and_the_description_is_a_quote_below_it(registry, team):
    reply = Run(registry, team, "/help", USER_ID)
    assert "<code>/gemini &lt;prompt&gt;</code>\n<blockquote>Ask Gemini anything.\n• " in reply
    assert "<code>/help</code>\n<blockquote>Show the commands you may use." in reply


def test_summary_comes_first_then_the_cases(registry, team):
    reply = Run(registry, team, "/help", USER_ID)
    block = re.search(r"<code>/rus.*?</blockquote>", reply, re.S).group(0)
    lines = html.unescape(block).split("\n")
    assert lines[1].startswith("<blockquote>Translate a web page or a text to Russian") and not lines[1].startswith("•")
    details = " ".join(lines[2:])
    assert "reply" in details and "link" in details and "text after the command" in details


def test_replies_media_and_links_are_explained(registry, team):
    text = html.unescape(Run(registry, team, "/help", USER_ID))
    for needle in ("As a reply", "picture", "Without a link in the command", "private chat"):
        assert needle in text


def test_stranger_gets_the_denial_not_help(registry, team):
    assert Run(registry, team, "/help", STRANGER_ID) == team.DeniedText()


def test_everything_from_help_entries_is_escaped(store):
    registry = BuildRegistry()
    from minionbot.commands import Command

    registry.Register(Command("evil", lambda c: "", help=(HelpEntry("/evil <b>", "a <script> & more", ("x < y",)),), minRole=Role.USER))
    reply = registry.Dispatch("help", [], OWNER_ID, None, store)
    assert "<script>" not in reply and "&lt;script&gt; &amp; more" in reply and "x &lt; y" in reply and "/evil &lt;b&gt;" in reply


def test_start_shows_the_same_help(registry, team):
    assert Run(registry, team, "/start", USER_ID) == Run(registry, team, "/help", USER_ID)


def test_the_help_is_split_between_entries_and_titles_stay_with_their_entry(registry, team):
    reply = Run(registry, team, "/help", OWNER_ID)
    parts = SplitHtmlBlocks(reply, limit=700)
    assert len(parts) > 2 and all(len(part) <= 700 for part in parts)
    assert "\n\n".join(parts) == reply
    for part in parts:
        assert part.count("<blockquote>") == part.count("</blockquote>")
        assert part.count("<b>") == part.count("</b>")


def test_the_full_help_fits_few_telegram_messages(registry, team):
    parts = SplitHtmlBlocks(Run(registry, team, "/help", OWNER_ID))
    assert 1 <= len(parts) <= 3 and all(len(part) <= 4096 for part in parts)


async def test_help_is_sent_as_html_in_parts_and_falls_back_to_plain_text(registry, team):
    message = FakeMessage("/help")
    await MakeCommandCallback(registry, team, "help")(FakeUpdate(message), FakeContext([]))
    assert message.reply_text.await_count >= 1
    assert all(call[1]["parse_mode"] == ParseMode.HTML for call in message.reply_text.call_args_list)

    message = FakeMessage("/help")
    message.reply_text = AsyncMock(side_effect=[BadRequest("can't parse entities")] + [None] * 10)
    await MakeCommandCallback(registry, team, "help")(FakeUpdate(message), FakeContext([]))
    plain = message.reply_text.call_args_list[1][0][0]
    assert "<b>" not in plain and "<blockquote>" not in plain and "<code>" not in plain and "&lt;" not in plain
    assert "/gemini <prompt>" in "".join(call[0][0] for call in message.reply_text.call_args_list[1:])


async def test_other_commands_are_still_plain_text(registry, team):
    message = FakeMessage("/whoami")
    await MakeCommandCallback(registry, team, "whoami")(FakeUpdate(message), FakeContext([]))
    assert "parse_mode" not in message.reply_text.call_args[1]

from minionbot.social.triggers import IGNORE, NO_LINKS, PROCESS, Decide, Incoming, IsMentioned

TWEET = "https://x.com/a/status/1"
BOT = "minionbot"


def test_mention_detection():
    assert IsMentioned("hey @MinionBot look", BOT)
    assert IsMentioned("@minionbot", BOT)
    assert not IsMentioned("@minionbot2", BOT)
    assert not IsMentioned("mail me at foo@minionbot.com", BOT)
    assert not IsMentioned("minionbot", BOT)


def test_private_message_with_link():
    decision = Decide(True, BOT, Incoming(f"see {TWEET}"))
    assert decision.kind == PROCESS and decision.links[0].url == TWEET


def test_private_message_without_link():
    assert Decide(True, BOT, Incoming("hello")).kind == NO_LINKS


def test_group_requires_mention():
    assert Decide(False, BOT, Incoming(TWEET)).kind == IGNORE
    assert Decide(False, BOT, Incoming(f"@{BOT} {TWEET}")).kind == PROCESS


def test_group_commands_ignored():
    assert Decide(False, BOT, Incoming(f"/help@{BOT}")).kind == IGNORE


def test_reply_mentioning_bot_uses_replied_message_links():
    decision = Decide(False, BOT, Incoming(f"@{BOT}"), Incoming(f"nice {TWEET}"))
    assert decision.kind == PROCESS and decision.links[0].url == TWEET and decision.mentioned


def test_reply_without_mention_ignored_in_group():
    assert Decide(False, BOT, Incoming("wow"), Incoming(TWEET)).kind == IGNORE


def test_own_links_win_over_replied_links():
    other = "https://x.com/b/status/2"
    decision = Decide(False, BOT, Incoming(f"@{BOT} {other}"), Incoming(TWEET))
    assert [l.url for l in decision.links] == [other]


def test_mention_without_any_links():
    decision = Decide(False, BOT, Incoming(f"@{BOT}"), Incoming("no links"))
    assert decision.kind == NO_LINKS and decision.mentioned


def test_hidden_text_link_in_replied_message():
    replied = Incoming("click", ["https://www.instagram.com/p/AAA/"])
    assert Decide(False, BOT, Incoming(f"@{BOT}"), replied).kind == PROCESS

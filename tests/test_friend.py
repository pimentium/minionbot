"""/friend: a stranger asks, the owner approves or denies with buttons, the stranger is told."""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType
from telegram.error import Forbidden
from telegram.ext import CallbackQueryHandler, CommandHandler

from minionbot.bot import FRIEND_PATTERN, BuildApplication, MakeFriendCommandCallback, MakeFriendDecisionCallback
from minionbot.config import Config
from minionbot.friends import COOLDOWN, NEW, PENDING, FriendRequest, FriendRequests
from minionbot.users import Role, UserRef

from tests.conftest import OWNER_ID, STRANGER_ID
from tests.test_bot import FakeContext, FakeMessage, FakeNotifier, FakeService, FakeUpdate

OTHER_ID = 4242


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def Sender(update, fullName="Mallory Jones", username="mallory"):
    update.effective_user.full_name, update.effective_user.username = fullName, username
    return update


async def Ask(registry, store, requests, userId=STRANGER_ID, username="mallory", fullName="Mallory Jones", notifier=None, sendError=None, chatId=None, chatType=ChatType.PRIVATE):
    message = FakeMessage("/friend")
    message.message_id = 77
    update = Sender(FakeUpdate(message, userId, chatType=chatType), fullName, username)
    update.effective_chat.id = chatId if chatId is not None else userId
    context = FakeContext([])
    context.bot.send_message = AsyncMock(side_effect=sendError)
    await MakeFriendCommandCallback(registry, store, requests, notifier)(update, context)
    return message, context


def Press(data, userId=OWNER_ID, text="Mallory Jones, @mallory, Telegram ID 9999\nasks for access to the bot. Approve?"):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = userId
    update.callback_query.from_user.username = None
    update.callback_query.message.text = text
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    context = FakeContext([])
    context.bot.send_message = AsyncMock()
    return update, context


@pytest.fixture
def ownerStore(store):
    store.Authenticate(OWNER_ID, "boss")  # the owner has talked to the bot, so the bot can message him
    return store


# ---- FriendRequests -----------------------------------------------------------------------------------------------

def test_requests_dedupe_and_cool_down_after_a_denial():
    clock = Clock()
    requests = FriendRequests(cooldown=100, clock=clock)
    request = FriendRequest(1, "a", "A")
    assert requests.Submit(request) == NEW and requests.Submit(request) == PENDING
    requests.Deny(1)
    assert requests.Submit(request) == COOLDOWN
    clock.now += 101
    assert requests.Submit(request) == NEW
    assert requests.Take(1) == request and requests.Take(1) is None


def test_name_candidates_are_valid_names_nicest_first():
    names = FriendRequest(7, "mallory", "Мария Ivanova").NameCandidates()
    assert names[0] == "mallory" and "7" in names and all(re.match(r"^[\w.-]{1,32}$", n) for n in names)
    assert FriendRequest(7, None, "!!!").NameCandidates() == ["7", "user7"]


# ---- the command ----------------------------------------------------------------------------------------------------

async def test_the_owner_gets_the_request_with_approve_and_deny_buttons(registry, ownerStore):
    message, context = await Ask(registry, ownerStore, FriendRequests())
    message.reply_text.assert_awaited_once()
    assert "asked my owner" in message.reply_text.call_args[0][0]
    args, kwargs = context.bot.send_message.call_args
    assert args[0] == OWNER_ID and "Mallory Jones" in args[1] and "@mallory" in args[1] and str(STRANGER_ID) in args[1]
    buttons = kwargs["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in buttons] == [f"friend:approve:{STRANGER_ID}", f"friend:deny:{STRANGER_ID}"]
    assert all(re.match(FRIEND_PATTERN, b.callback_data) for b in buttons)


async def test_a_second_request_while_waiting_does_not_bother_the_owner_again(registry, ownerStore):
    requests = FriendRequests()
    await Ask(registry, ownerStore, requests)
    message, context = await Ask(registry, ownerStore, requests)
    assert "already waiting" in message.reply_text.call_args[0][0] and context.bot.send_message.await_count == 0


async def test_existing_users_are_told_they_have_access(registry, ownerStore):
    message, context = await Ask(registry, ownerStore, FriendRequests(), userId=OWNER_ID)
    assert "already have access" in message.reply_text.call_args[0][0] and context.bot.send_message.await_count == 0


async def test_without_a_reachable_owner_nothing_is_sent(registry, tmp_path):
    from minionbot.users import UserStore

    store = UserStore(tmp_path / "u.json")
    store.EnsureInitialUser("@bossman")  # known by username only: no chat ID yet
    message, context = await Ask(registry, store, FriendRequests())
    assert "hasn't been in touch" in message.reply_text.call_args[0][0] and context.bot.send_message.await_count == 0


async def test_if_the_owner_cannot_be_messaged_the_request_is_withdrawn_and_reported(registry, ownerStore):
    requests, notifier = FriendRequests(), FakeNotifier()
    message, _ = await Ask(registry, ownerStore, requests, notifier=notifier, sendError=Forbidden("bot was blocked"))
    assert "couldn't reach" in message.reply_text.call_args[0][0] and requests.pending == {}
    notifier.ReportFailure.assert_awaited_once()


# ---- the owner's answer ---------------------------------------------------------------------------------------------

async def Request(registry, store, requests, **kwargs):
    await Ask(registry, store, requests, **kwargs)


async def test_approval_adds_the_user_and_tells_both_sides(registry, ownerStore):
    requests = FriendRequests()
    await Request(registry, ownerStore, requests)
    update, context = Press(f"friend:approve:{STRANGER_ID}")
    await MakeFriendDecisionCallback(ownerStore, requests)(update, context)
    user = ownerStore.Find(str(STRANGER_ID))
    assert user is not None and user.name == "mallory" and user.role == Role.USER and user.username == "mallory"
    assert "Approved" in update.callback_query.edit_message_text.call_args[0][0]
    assert update.callback_query.edit_message_text.call_args[1].get("reply_markup") is None  # buttons are gone
    context.bot.send_message.assert_awaited_once()
    assert context.bot.send_message.call_args[0][0] == STRANGER_ID and "approved" in context.bot.send_message.call_args[0][1]
    assert ownerStore.Authenticate(STRANGER_ID, "mallory") is user


async def test_denial_adds_nobody_and_says_so(registry, ownerStore):
    requests = FriendRequests()
    await Request(registry, ownerStore, requests)
    update, context = Press(f"friend:deny:{STRANGER_ID}")
    await MakeFriendDecisionCallback(ownerStore, requests)(update, context)
    assert ownerStore.Find(str(STRANGER_ID)) is None
    assert context.bot.send_message.call_args[0] == (STRANGER_ID, "Your request was denied.")
    assert "Denied" in update.callback_query.edit_message_text.call_args[0][0]
    message, _ = await Ask(registry, ownerStore, requests)  # asking again right away is refused
    assert "denied your last request" in message.reply_text.call_args[0][0]


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
async def test_only_the_owner_can_answer(registry, ownerStore, role):
    ownerStore.Add(UserRef(telegramId=OTHER_ID), name="other", role=role)
    requests = FriendRequests()
    await Request(registry, ownerStore, requests)
    update, context = Press(f"friend:approve:{STRANGER_ID}", userId=OTHER_ID)
    await MakeFriendDecisionCallback(ownerStore, requests)(update, context)
    assert ownerStore.Find(str(STRANGER_ID)) is None and context.bot.send_message.await_count == 0
    assert update.callback_query.answer.call_args[1]["show_alert"] is True and STRANGER_ID in requests.pending
    update.callback_query.edit_message_text.assert_not_awaited()


async def test_strangers_cannot_approve_themselves(registry, ownerStore):
    requests = FriendRequests()
    await Request(registry, ownerStore, requests)
    update, context = Press(f"friend:approve:{STRANGER_ID}", userId=STRANGER_ID)
    await MakeFriendDecisionCallback(ownerStore, requests)(update, context)
    assert ownerStore.Find(str(STRANGER_ID)) is None


async def test_a_name_clash_falls_back_to_another_name(registry, ownerStore):
    ownerStore.Add(UserRef(telegramId=OTHER_ID), name="mallory")
    requests = FriendRequests()
    await Request(registry, ownerStore, requests, username="mallory2x", fullName="mallory")
    update, context = Press(f"friend:approve:{STRANGER_ID}")
    await MakeFriendDecisionCallback(ownerStore, requests)(update, context)
    assert ownerStore.Find(str(STRANGER_ID)).name == "mallory2x"
    ownerStore.Remove(ownerStore.Find(str(STRANGER_ID)))
    ownerStore.Add(UserRef(telegramId=OTHER_ID + 1), name="mallory2x")
    await Request(registry, ownerStore, requests, username="mallory2x", fullName="mallory")
    update, context = Press(f"friend:approve:{STRANGER_ID}")
    await MakeFriendDecisionCallback(ownerStore, requests)(update, context)
    assert ownerStore.Find(str(STRANGER_ID)).name == str(STRANGER_ID)


async def test_after_a_restart_the_id_alone_is_enough_to_approve(registry, ownerStore):
    update, context = Press(f"friend:approve:{STRANGER_ID}")
    await MakeFriendDecisionCallback(ownerStore, FriendRequests())(update, context)
    assert ownerStore.Find(str(STRANGER_ID)).name == str(STRANGER_ID)


async def test_pressing_approve_twice_adds_once(registry, ownerStore):
    requests = FriendRequests()
    await Request(registry, ownerStore, requests)
    for _ in range(2):
        update, context = Press(f"friend:approve:{STRANGER_ID}")
        await MakeFriendDecisionCallback(ownerStore, requests)(update, context)
    assert len([u for u in ownerStore.List() if u.telegramId == STRANGER_ID]) == 1
    assert "Already has access" in update.callback_query.edit_message_text.call_args[0][0] and context.bot.send_message.await_count == 0


async def test_a_blocked_requester_does_not_break_the_decision(registry, ownerStore):
    requests = FriendRequests()
    await Request(registry, ownerStore, requests)
    update, context = Press(f"friend:approve:{STRANGER_ID}")
    context.bot.send_message = AsyncMock(side_effect=Forbidden("blocked"))
    await MakeFriendDecisionCallback(ownerStore, requests)(update, context)
    assert ownerStore.Find(str(STRANGER_ID)) is not None


async def test_garbage_callback_data_is_ignored(registry, ownerStore):
    update, context = Press("friend:approve:notanumber")
    await MakeFriendDecisionCallback(ownerStore, FriendRequests())(update, context)
    update.callback_query.answer.assert_not_awaited()


# ---- wiring -------------------------------------------------------------------------------------------------------------

def test_friend_is_wired_for_all_chats_and_the_buttons(registry, store):
    app = BuildApplication(Config("123:FAKE", "1", "x"), store, registry, FakeService())
    handlers = app.handlers[0]
    assert any(isinstance(h, CommandHandler) and "friend" in h.commands for h in handlers)
    assert any(isinstance(h, CallbackQueryHandler) for h in handlers)
    assert registry.commands["friend"].inGroups and registry.commands["friend"].needsChat


def test_strangers_are_pointed_to_friend(store):
    assert "/friend" in store.DeniedText()


# ---- in group chats ---------------------------------------------------------------------------------------------------

GROUP_ID = -100500


async def test_in_a_group_the_owner_is_asked_privately_and_the_answer_is_a_reply_in_the_group(registry, ownerStore):
    requests = FriendRequests()
    message, context = await Ask(registry, ownerStore, requests, chatId=GROUP_ID, chatType=ChatType.GROUP)
    assert context.bot.send_message.call_args[0][0] == OWNER_ID and "asked my owner" in message.reply_text.call_args[0][0]
    update, context = Press(f"friend:approve:{STRANGER_ID}")
    await MakeFriendDecisionCallback(ownerStore, requests)(update, context)
    args, kwargs = context.bot.send_message.call_args
    assert args[0] == GROUP_ID and "approved" in args[1]
    assert kwargs["reply_parameters"].message_id == 77 and kwargs["reply_parameters"].allow_sending_without_reply


async def test_a_denial_is_posted_in_the_group_too(registry, ownerStore):
    requests = FriendRequests()
    await Ask(registry, ownerStore, requests, chatId=GROUP_ID, chatType=ChatType.GROUP)
    update, context = Press(f"friend:deny:{STRANGER_ID}")
    await MakeFriendDecisionCallback(ownerStore, requests)(update, context)
    assert context.bot.send_message.call_args[0] == (GROUP_ID, "Your request was denied.")


async def test_if_the_group_is_unreachable_the_answer_goes_to_the_private_chat(registry, ownerStore):
    requests = FriendRequests()
    await Ask(registry, ownerStore, requests, chatId=GROUP_ID, chatType=ChatType.GROUP)
    update, context = Press(f"friend:approve:{STRANGER_ID}")
    context.bot.send_message = AsyncMock(side_effect=[Forbidden("kicked"), None])
    await MakeFriendDecisionCallback(ownerStore, requests)(update, context)
    assert [call[0][0] for call in context.bot.send_message.call_args_list] == [GROUP_ID, STRANGER_ID]


async def test_friend_after_a_mention_in_a_group_is_routed_to_the_same_callback(registry, store):
    from minionbot.bot import MakeMessageCallback

    seen = []

    async def Friend(update, context):
        seen.append(update)

    handler = MakeMessageCallback(store, FakeService(), None, None, {"friend": Friend})
    message = FakeMessage("@minionbot /friend")
    context = FakeContext()
    await handler(FakeUpdate(message, STRANGER_ID, chatType=ChatType.GROUP), context)
    assert len(seen) == 1

"""Role model: unknown < user < admin < owner, each including everything below it."""

import pytest

from minionbot.users import SOCIAL_LINKS_MIN_ROLE, Role, UserRef

from tests.conftest import OWNER_ID, STRANGER_ID

ADMIN_ID, USER_ID = 3001, 3002


@pytest.fixture
def team(store):
    store.Add(UserRef(telegramId=ADMIN_ID), name="adm", role=Role.ADMIN)
    store.Add(UserRef(telegramId=USER_ID), name="usr", role=Role.USER)
    return store


def Run(registry, store, text, telegramId=OWNER_ID, username=None, control=None):
    name, *args = text.lstrip("/").split()
    return registry.Dispatch(name, args, telegramId, username, store, control=control)


def Allowed(registry, store, text, telegramId):
    reply = Run(registry, store, text, telegramId, control=object())
    return store.DeniedText() != reply and "don't have permission" not in reply


ROLE_IDS = {"unknown": STRANGER_ID, "user": USER_ID, "admin": ADMIN_ID, "owner": OWNER_ID}

# command text -> lowest role allowed to run it (the spec)
MATRIX = {
    "/whoami": "unknown",
    "/social https://x.com/a/status/1": "user",
    "/url https://example.com/article": "user",
    "/start": "user",
    "/help": "user",
    "/users list": "admin",
    "/users add 777": "admin",
    "/users update 777 admin": "admin",
    "/users remove 777": "admin",
    "/restart": "owner",
    "/shutdown": "owner",
}


@pytest.mark.parametrize("command", MATRIX)
@pytest.mark.parametrize("role", ["unknown", "user", "admin", "owner"])
def test_command_access_matrix(registry, team, command, role):
    order = ["unknown", "user", "admin", "owner"]
    expected = order.index(role) >= order.index(MATRIX[command])
    # /users add/update/remove need a target; make sure it exists so a permitted call is not an input error
    if team.Find("777") is None:
        team.Add(UserRef(telegramId=777), name="target")
    assert Allowed(registry, team, command, ROLE_IDS[role]) is expected


def test_commands_registered_with_the_specified_minimum_roles(registry):
    minimum = {name: c.minRole for name, c in registry.commands.items()}
    assert minimum == {
        "whoami": Role.UNKNOWN, "start": Role.USER, "help": Role.USER, "social": Role.USER, "url": Role.USER,
        "users": Role.ADMIN, "restart": Role.OWNER, "shutdown": Role.OWNER,
    }
    assert SOCIAL_LINKS_MIN_ROLE == Role.USER


def test_permissions_are_cumulative(registry):
    lists = {role: registry.Permissions(role) for role in Role}
    assert lists[Role.UNKNOWN] == ["/whoami"]
    for lower, higher in zip(Role, list(Role)[1:]):
        assert set(lists[lower]) < set(lists[higher])
    assert "social network links" not in lists[Role.UNKNOWN] and "social network links" in lists[Role.USER]
    assert "/restart" in lists[Role.OWNER] and "/restart" not in lists[Role.ADMIN]


def test_help_shows_only_what_the_role_may_use(registry, team):
    userHelp = Run(registry, team, "/help", USER_ID)
    adminHelp = Run(registry, team, "/help", ADMIN_ID)
    ownerHelp = Run(registry, team, "/help", OWNER_ID)
    assert "/users" not in userHelp and "/whoami" in userHelp
    assert "/users update" in adminHelp and "/restart" not in adminHelp
    assert "/restart" in ownerHelp and "/shutdown" in ownerHelp


# ---- /whoami ---------------------------------------------------------------------------------------

def test_whoami_for_unknown_keeps_the_original_format(registry, team):
    assert Run(registry, team, "/whoami", STRANGER_ID, "mallory") == (
        f"Telegram ID: {STRANGER_ID}\nUsername: @mallory\nAccess: not granted"
    )


@pytest.mark.parametrize(
    "role,permissions",
    [
        ("user", "/start, /help, /whoami, /social, /url, social network links"),
        ("admin", "/start, /help, /whoami, /social, /url, /users, social network links"),
        ("owner", "/start, /help, /whoami, /social, /url, /users, /restart, /shutdown, social network links"),
    ],
)
def test_whoami_shows_role_and_permissions(registry, team, role, permissions):
    reply = Run(registry, team, "/whoami", ROLE_IDS[role])
    assert f"Role: {role}\n" in reply + "\n" and f"Permissions: {permissions}" in reply
    assert "Access: granted as" in reply and reply.startswith(f"Telegram ID: {ROLE_IDS[role]}")


# ---- /users add / update --------------------------------------------------------------------------

def test_add_accepts_id_or_username_and_role(registry, store):
    assert "[user]" in Run(registry, store, "/users add 500")
    assert "[admin]" in Run(registry, store, "/users add 501 admin")
    assert "[user]" in Run(registry, store, "/users add @carol99 user")
    assert "[admin]" in Run(registry, store, "/users add carol100 ADMIN carol")  # case-insensitive role, optional name
    assert store.Find("carol").role == Role.ADMIN


@pytest.mark.parametrize("role", ["owner", "unknown", "root"])
def test_add_rejects_owner_unknown_and_invented_roles(registry, store, role):
    reply = Run(registry, store, f"/users add 600 {role}")
    assert reply.startswith("Error") and store.Find("600") is None


def test_update_changes_role_by_id_username_or_name(registry, team):
    Run(registry, team, "/users add @dave77")
    assert "[admin]" in Run(registry, team, f"/users update {USER_ID} admin")
    assert "[user]" in Run(registry, team, "/users update adm user")
    assert "[admin]" in Run(registry, team, "/users update @dave77 admin")
    assert team.Find(str(USER_ID)).role == Role.ADMIN and team.Find("adm").role == Role.USER


def test_update_requires_a_role_and_a_known_user(registry, team):
    assert Run(registry, team, f"/users update {USER_ID}").startswith("Usage")
    assert Run(registry, team, "/users update").startswith("Usage")
    assert Run(registry, team, "/users update ghost admin").startswith("Error: No user matches")
    assert Run(registry, team, f"/users update {USER_ID} superman").startswith("Error: Unknown role")
    assert team.Find(str(USER_ID)).role == Role.USER


def test_update_cannot_assign_owner_or_touch_the_owner(registry, team):
    assert "owner" in Run(registry, team, f"/users update {USER_ID} owner", ADMIN_ID).lower()
    assert team.Find(str(USER_ID)).role == Role.USER
    reply = Run(registry, team, f"/users update {OWNER_ID} user", ADMIN_ID)
    assert reply.startswith("Error") and team.GetOwner().role == Role.OWNER


def test_nobody_changes_their_own_role(registry, team):
    assert Run(registry, team, f"/users update {ADMIN_ID} user", ADMIN_ID) == "Error: You cannot change your own role."
    assert Run(registry, team, f"/users update {OWNER_ID} admin") == "Error: You cannot change your own role."
    assert team.Find(str(ADMIN_ID)).role == Role.ADMIN


def test_admin_cannot_remove_the_owner(registry, team):
    assert Run(registry, team, f"/users remove {OWNER_ID}", ADMIN_ID).startswith("Error")
    assert team.GetOwner() is not None


def test_role_changes_take_effect_immediately(registry, team):
    assert "permission" in Run(registry, team, "/users list", USER_ID)
    Run(registry, team, f"/users update {USER_ID} admin")
    assert "Users (" in Run(registry, team, "/users list", USER_ID)
    Run(registry, team, f"/users update {USER_ID} user", ADMIN_ID)
    assert "permission" in Run(registry, team, "/users list", USER_ID)


# ---- social link handling is gated by role through the real message handler --------------------------

@pytest.mark.parametrize("role,served", [("unknown", False), ("user", True), ("admin", True), ("owner", True)])
async def test_social_links_need_the_user_role(team, tmp_path, role, served):
    from telegram.constants import ChatType

    from minionbot.bot import MakeMessageCallback
    from tests.test_bot import FakeContext, FakeMessage, FakeService, FakeUpdate, MakePost, TWEET

    service = FakeService(MakePost(tmp_path))
    message = FakeMessage(TWEET)
    await MakeMessageCallback(team, service)(FakeUpdate(message, ROLE_IDS[role], chatType=ChatType.PRIVATE), FakeContext())
    assert (service.loaded == [TWEET]) is served
    if not served:
        message.reply_text.assert_awaited_once_with(team.DeniedText())

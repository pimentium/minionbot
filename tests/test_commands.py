from minionbot.commands import UNKNOWN_COMMAND_TEXT, Command, CommandRegistry
from minionbot.users import UserRef

from tests.conftest import OWNER_ID, STRANGER_ID


def Run(registry, store, text, telegramId=OWNER_ID, username=None):
    name, *args = text.lstrip("/").split()
    return registry.Dispatch(name, args, telegramId, username, store)


def test_stranger_is_denied_everything_but_whoami(registry, store):
    for text in ["/help", "/users list", "/start", "/nonexistent"]:
        assert Run(registry, store, text, STRANGER_ID) == store.DeniedText()
    reply = Run(registry, store, "/whoami", STRANGER_ID, "bob")
    assert str(STRANGER_ID) in reply and "not granted" in reply
    assert registry.DispatchFallback(STRANGER_ID, None, store) == store.DeniedText()


def test_known_user_unknown_command(registry, store):
    assert Run(registry, store, "/nope") == UNKNOWN_COMMAND_TEXT
    assert registry.DispatchFallback(OWNER_ID, None, store) == UNKNOWN_COMMAND_TEXT


def test_help_lists_commands(registry, store):
    reply = Run(registry, store, "/help")
    for expected in ["/help", "/whoami", "/users list", "/users add", "/users remove"]:
        assert expected in reply
    assert Run(registry, store, "/start") == reply


def test_help_hides_commands_without_permission(registry, store):
    store.Add(UserRef(telegramId=2), name="limited", permissions=["other"])
    reply = Run(registry, store, "/help", 2)
    assert "/users" not in reply and "/help" in reply
    assert "permission" in Run(registry, store, "/users list", 2)


def test_users_list_add_remove_flow(registry, store):
    assert "owner" in Run(registry, store, "/users list")
    reply = Run(registry, store, "/users add 2002 anna perms=admin,notes")
    assert reply.startswith("Added anna") and "admin, notes" in reply
    assert "anna" in Run(registry, store, "/users list")
    assert Run(registry, store, "/users remove anna") == "Removed anna."
    assert "anna" not in Run(registry, store, "/users list")


def test_new_user_has_all_commands_by_default(registry, store):
    Run(registry, store, "/users add 2002 anna")
    assert "anna" in Run(registry, store, "/users list", 2002)


def test_add_username_notes_linking(registry, store):
    reply = Run(registry, store, "/users add @frank99")
    assert "linked" in reply
    assert "frank99" in Run(registry, store, "/users list", 77, "Frank99") 


def test_users_errors_and_usage(registry, store):
    assert Run(registry, store, "/users").startswith("Usage")
    assert Run(registry, store, "/users bogus").startswith("Usage")
    assert Run(registry, store, "/users add").startswith("Usage")
    assert Run(registry, store, "/users add !!!").startswith("Error")
    assert Run(registry, store, "/users add 5 a b").startswith("Error")
    assert Run(registry, store, "/users remove ghost").startswith("Error")
    assert Run(registry, store, f"/users remove {OWNER_ID}") == "Error: You cannot remove yourself."


def test_handler_crash_is_contained(store):
    registry = CommandRegistry()
    registry.Register(Command("boom", lambda ctx: 1 / 0))
    assert Run(registry, store, "/boom") == "Something went wrong."


def test_duplicate_registration_rejected():
    import pytest

    registry = CommandRegistry()
    registry.Register(Command("a", lambda ctx: ""))
    with pytest.raises(ValueError):
        registry.Register(Command("a", lambda ctx: ""))

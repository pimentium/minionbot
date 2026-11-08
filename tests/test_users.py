import json

import pytest

from minionbot.users import ALL_PERMISSIONS, ParseUserRef, UserError, UserRef, UserStore


def test_parse_user_ref():
    assert ParseUserRef("123") == UserRef(telegramId=123)
    assert ParseUserRef("@Alice_1") == UserRef(username="alice_1")
    assert ParseUserRef("alice_1") == UserRef(username="alice_1")


@pytest.mark.parametrize("bad", ["", "0", "@ab", "-5", "a b", "@1abcde"])
def test_parse_user_ref_invalid(bad):
    with pytest.raises(UserError):
        ParseUserRef(bad)


def test_initial_user_created_once(tmp_path):
    store = UserStore(tmp_path / "u.json")
    first = store.EnsureInitialUser("42")
    again = store.EnsureInitialUser("42")
    assert first is again
    assert len(store.List()) == 1
    assert first.permissions == [ALL_PERMISSIONS]


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "sub" / "u.json"
    store = UserStore(path)
    store.Add(UserRef(telegramId=7), name="bob", permissions=["admin"])
    reloaded = UserStore(path)
    assert [u.ToDict() for u in reloaded.List()] == [u.ToDict() for u in store.List()]
    assert (path.stat().st_mode & 0o777) == 0o600


def test_corrupt_file_is_not_overwritten(tmp_path):
    path = tmp_path / "u.json"
    path.write_text("{not json")
    with pytest.raises(UserError):
        UserStore(path)
    assert path.read_text() == "{not json"


def test_duplicates_rejected(store):
    store.Add(UserRef(telegramId=5), name="eve")
    with pytest.raises(UserError):
        store.Add(UserRef(telegramId=5), name="other")
    with pytest.raises(UserError):
        store.Add(UserRef(telegramId=6), name="EVE")
    store.Add(UserRef(username="carol1"))
    with pytest.raises(UserError):
        store.Add(UserRef(username="carol1"), name="x")


def test_invalid_name_and_permission(store):
    with pytest.raises(UserError):
        store.Add(UserRef(telegramId=5), name="bad name")
    with pytest.raises(UserError):
        store.Add(UserRef(telegramId=5), name="ok", permissions=["Bad Perm"])


def test_authenticate_by_id(store):
    assert store.Authenticate(1001, None).name == "owner"
    assert store.Authenticate(2, "whoever") is None


def test_username_linked_on_first_contact_then_id_only(tmp_path):
    path = tmp_path / "u.json"
    store = UserStore(path)
    store.Add(UserRef(username="alice1"))
    assert store.Authenticate(55, "Alice1").telegramId == 55
    # Username later taken by someone else: no access.
    assert store.Authenticate(66, "alice1") is None
    # Link survived to disk.
    assert UserStore(path).Authenticate(55, None) is not None


def test_find_by_id_username_name(store):
    store.Add(UserRef(username="dave12"), name="dave")
    assert store.Find("dave").username == "dave12"
    assert store.Find("@dave12").name == "dave"
    assert store.Find("1001").name == "owner"
    assert store.Find("nobody") is None


def test_remove_and_last_user_protected(store):
    other = store.Add(UserRef(telegramId=8), name="x")
    store.Remove(other)
    assert len(store.List()) == 1
    with pytest.raises(UserError):
        store.Remove(store.List()[0])


def test_file_contains_no_extra_secrets(store):
    data = json.loads(store.path.read_text())
    assert set(data["users"][0]) == {"name", "telegramId", "username", "permissions"}

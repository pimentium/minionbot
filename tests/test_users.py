import json

import pytest

from minionbot.users import ASSIGNABLE_ROLES, ParseRole, ParseUserRef, Role, UserError, UserRef, UserStore


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
    assert first.role == Role.OWNER


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "sub" / "u.json"
    store = UserStore(path)
    store.EnsureInitialUser("1")
    store.Add(UserRef(telegramId=7), name="bob", role=Role.ADMIN)
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
        store.Add(UserRef(telegramId=5), name="ok", role=Role.UNKNOWN)


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
    assert set(data["users"][0]) == {"name", "telegramId", "username", "role"}


def test_owner_and_denied_text(tmp_path):
    store = UserStore(tmp_path / "u.json")
    owner = store.EnsureInitialUser("42")
    assert owner.role == Role.OWNER and store.GetOwner() is owner
    assert store.DeniedText() == "Please ask my owner owner to allow me to help you too."
    store.Authenticate(42, "MyName")  # owner's username is learned on contact
    assert store.DeniedText() == "Please ask my owner @myname to allow me to help you too."
    assert UserStore(tmp_path / "u.json").DeniedText() == store.DeniedText()


# ---- roles -------------------------------------------------------------------------------------

def test_role_order_and_parsing():
    assert Role.UNKNOWN < Role.USER < Role.ADMIN < Role.OWNER
    assert ParseRole(" Admin ") == Role.ADMIN and ParseRole("USER") == Role.USER
    assert ASSIGNABLE_ROLES == (Role.USER, Role.ADMIN)
    with pytest.raises(UserError, match="Roles: user, admin"):
        ParseRole("superman")


def test_add_defaults_to_user_role(store):
    assert store.Add(UserRef(telegramId=5)).role == Role.USER
    assert store.Add(UserRef(telegramId=6), role=Role.ADMIN).role == Role.ADMIN


@pytest.mark.parametrize("role", [Role.OWNER, Role.UNKNOWN])
def test_owner_and_unknown_cannot_be_assigned(store, role):
    with pytest.raises(UserError):
        store.Add(UserRef(telegramId=5), role=role)
    user = store.Add(UserRef(telegramId=6))
    with pytest.raises(UserError):
        store.UpdateRole(user, role)
    assert user.role == Role.USER


def test_update_role_persists_and_protects_owner(store):
    user = store.Add(UserRef(telegramId=5))
    store.UpdateRole(user, Role.ADMIN)
    assert UserStore(store.path).Find("5").role == Role.ADMIN
    with pytest.raises(UserError, match="owner"):
        store.UpdateRole(store.GetOwner(), Role.USER)


def test_owner_cannot_be_removed(store):
    with pytest.raises(UserError, match="owner"):
        store.Remove(store.GetOwner())


def test_initial_user_becomes_the_only_owner(tmp_path):
    store = UserStore(tmp_path / "u.json")
    first = store.EnsureInitialUser("1")
    friend = store.Add(UserRef(telegramId=2), role=Role.ADMIN)
    second = store.EnsureInitialUser("2")  # the start user changed
    assert second is friend and friend.role == Role.OWNER and first.role == Role.ADMIN
    assert [u.role for u in UserStore(tmp_path / "u.json").List()].count(Role.OWNER) == 1


def test_existing_user_is_promoted_to_owner_by_username_start_user(tmp_path):
    store = UserStore(tmp_path / "u.json")
    store.EnsureInitialUser("1")
    store.Add(UserRef(username="boss12"))
    assert store.EnsureInitialUser("@boss12").role == Role.OWNER
    assert store.Find("1").role == Role.ADMIN


def test_initial_user_start_is_idempotent(tmp_path):
    store = UserStore(tmp_path / "u.json")
    store.EnsureInitialUser("1")
    mtime = store.path.stat().st_mtime_ns
    store.EnsureInitialUser("1")
    assert store.path.stat().st_mtime_ns == mtime and len(store.List()) == 1


# ---- migration of files written before roles existed ---------------------------------------------

def OldFile(tmp_path, users):
    path = tmp_path / "u.json"
    path.write_text(json.dumps({"users": users}))
    return path


def test_old_file_migration_never_grants_more_than_before(tmp_path):
    path = OldFile(tmp_path, [
        {"name": "boss", "telegramId": 1, "permissions": ["*"], "owner": True},
        {"name": "everything", "telegramId": 2, "permissions": ["*"]},
        {"name": "adm", "telegramId": 3, "permissions": ["admin", "notes"]},
        {"name": "limited", "telegramId": 4, "permissions": ["notes"]},
    ])
    roles = {u.name: u.role for u in UserStore(path).List()}
    assert roles == {"boss": Role.OWNER, "everything": Role.USER, "adm": Role.ADMIN, "limited": Role.USER}


def test_old_file_without_owner_flag_has_no_owner_until_start(tmp_path):
    path = OldFile(tmp_path, [{"name": "a", "telegramId": 1, "permissions": ["*"]}])
    store = UserStore(path)
    assert store.GetOwner() is None
    assert store.EnsureInitialUser("1").role == Role.OWNER
    assert "permissions" not in path.read_text()  # rewritten in the new format

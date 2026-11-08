import pytest

from minionbot.builtin import BuildRegistry
from minionbot.users import UserStore

OWNER_ID = 1001
STRANGER_ID = 9999


@pytest.fixture
def store(tmp_path):
    userStore = UserStore(tmp_path / "users.json")
    userStore.EnsureInitialUser(str(OWNER_ID))
    return userStore


@pytest.fixture
def registry():
    return BuildRegistry()

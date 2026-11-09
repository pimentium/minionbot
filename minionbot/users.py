"""User model and JSON-backed user store."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

ALL_PERMISSIONS = "*"
ADMIN_PERMISSION = "admin"

USERNAME_PATTERN = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{4,31})$")
NAME_PATTERN = re.compile(r"^[\w.-]{1,32}$")
PERMISSION_PATTERN = re.compile(r"^(\*|[a-z][a-z0-9_-]{0,31})$")


class UserError(Exception):
    """A user-facing problem (bad input, duplicate, unreadable store)."""


@dataclass(frozen=True)
class UserRef:
    """Reference to a Telegram user: a numeric ID or a username (stored lowercase, no '@')."""

    telegramId: int | None = None
    username: str | None = None


def ParseUserRef(text: str) -> UserRef:
    """Parse '123456789' or '@name' / 'name' into a UserRef."""
    text = text.strip()
    if text.isdigit() and int(text) > 0:
        return UserRef(telegramId=int(text))
    match = USERNAME_PATTERN.match(text)
    if match:
        return UserRef(username=match.group(1).lower())
    raise UserError(f"'{text}' is not a valid Telegram ID or @username.")


@dataclass
class User:
    name: str
    telegramId: int | None = None
    username: str | None = None
    permissions: list[str] = field(default_factory=lambda: [ALL_PERMISSIONS])
    owner: bool = False

    def HasPermission(self, permission: str) -> bool:
        return ALL_PERMISSIONS in self.permissions or permission in self.permissions

    def ToDict(self) -> dict:
        return {
            "name": self.name,
            "telegramId": self.telegramId,
            "username": self.username,
            "permissions": list(self.permissions),
            "owner": self.owner,
        }

    @staticmethod
    def FromDict(data: dict) -> User:
        return User(
            name=data["name"],
            telegramId=data.get("telegramId"),
            username=data.get("username"),
            permissions=list(data.get("permissions") or [ALL_PERMISSIONS]),
            owner=bool(data.get("owner", False)),
        )

    def Describe(self) -> str:
        ident = []
        if self.telegramId is not None:
            ident.append(f"id {self.telegramId}")
        elif self.username:
            ident.append("id not linked yet")
        if self.username:
            ident.append(f"@{self.username}")
        return f"{self.name} ({', '.join(ident)}) [{', '.join(self.permissions)}]"


class UserStore:
    """Users persisted in a JSON file (atomic writes). Unknown users get no access."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.users: list[User] = []
        self._Load()

    # ---- persistence -------------------------------------------------

    def _Load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.users = [User.FromDict(item) for item in data["users"]]
        except (OSError, ValueError, KeyError, TypeError) as error:
            # Refuse to continue: silently starting empty would overwrite the file.
            raise UserError(f"Cannot read users file {self.path}: {error}") from error

    def _Save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"users": [u.ToDict() for u in self.users]}, indent=2)
        fd, tmpName = tempfile.mkstemp(dir=self.path.parent, prefix=".users-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.chmod(tmpName, 0o600)
            os.replace(tmpName, self.path)
        except BaseException:
            Path(tmpName).unlink(missing_ok=True)
            raise

    # ---- queries -----------------------------------------------------

    def List(self) -> list[User]:
        with self.lock:
            return list(self.users)

    def Find(self, text: str) -> User | None:
        """Find a user by numeric ID, @username or name."""
        with self.lock:
            try:
                ref = ParseUserRef(text)
            except UserError:
                ref = UserRef()
            for user in self.users:
                if ref.telegramId is not None and user.telegramId == ref.telegramId:
                    return user
                if ref.username is not None and user.username == ref.username:
                    return user
                if user.name.lower() == text.strip().lower():
                    return user
            return None

    def Authenticate(self, telegramId: int, username: str | None) -> User | None:
        """Return the configured user for an incoming Telegram sender, or None.

        A user added by @username is linked to the numeric ID on first contact; after
        that only the ID matches, so a later owner of the same username gets nothing.
        """
        with self.lock:
            for user in self.users:
                if user.telegramId == telegramId:
                    self._RefreshUsername(user, username)
                    return user
            if username:
                lowered = username.lower()
                for user in self.users:
                    if user.telegramId is None and user.username == lowered:
                        user.telegramId = telegramId
                        self._Save()
                        return user
            return None

    def _RefreshUsername(self, user: User, username: str | None) -> None:
        """Keep the stored username current (needed to name the owner in denial messages)."""
        if username and username.lower() != user.username:
            user.username = username.lower()
            self._Save()

    def GetOwner(self) -> User | None:
        """The owner: the user flagged as such, else the first user."""
        with self.lock:
            for user in self.users:
                if user.owner:
                    return user
            return self.users[0] if self.users else None

    def DeniedText(self) -> str:
        owner = self.GetOwner()
        if owner is None:
            who = "the owner"
        else:
            who = f"@{owner.username}" if owner.username else owner.name
        return f"Please ask my owner {who} to allow me to help you too."

    # ---- mutations ---------------------------------------------------

    def Add(self, ref: UserRef, name: str | None = None, permissions: list[str] | None = None) -> User:
        with self.lock:
            name = name or ref.username or str(ref.telegramId)
            if not NAME_PATTERN.match(name):
                raise UserError("Name must be 1-32 chars: letters, digits, '_', '.', '-'.")
            permissions = permissions or [ALL_PERMISSIONS]
            for permission in permissions:
                if not PERMISSION_PATTERN.match(permission):
                    raise UserError(f"Invalid permission '{permission}'.")
            for existing in self.users:
                if ref.telegramId is not None and existing.telegramId == ref.telegramId:
                    raise UserError(f"User with ID {ref.telegramId} already exists ({existing.name}).")
                if ref.username is not None and existing.username == ref.username:
                    raise UserError(f"User @{ref.username} already exists ({existing.name}).")
                if existing.name.lower() == name.lower():
                    raise UserError(f"Name '{name}' is already taken.")
            user = User(name=name, telegramId=ref.telegramId, username=ref.username, permissions=permissions)
            self.users.append(user)
            self._Save()
            return user

    def Remove(self, user: User) -> None:
        with self.lock:
            if user not in self.users:
                raise UserError("No such user.")
            if len(self.users) == 1:
                raise UserError("Cannot remove the last user.")
            self.users.remove(user)
            self._Save()

    def EnsureInitialUser(self, reference: str) -> User:
        """Create the owner on start if no matching user exists yet."""
        with self.lock:
            ref = ParseUserRef(reference)
            for user in self.users:
                if (ref.telegramId is not None and user.telegramId == ref.telegramId) or (
                    ref.username is not None and user.username == ref.username
                ):
                    return user
            name = "owner"
            while any(u.name.lower() == name for u in self.users):
                name += "_"
            created = self.Add(ref, name=name, permissions=[ALL_PERMISSIONS])
            created.owner = True
            self._Save()
            return created

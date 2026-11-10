"""User model and JSON-backed user store."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

USERNAME_PATTERN = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{4,31})$")
NAME_PATTERN = re.compile(r"^[\w.-]{1,32}$")


class UserError(Exception):
    """A user-facing problem (bad input, duplicate, unreadable store)."""


class Role(IntEnum):
    """Each role includes everything the previous ones may do (compare with >=)."""

    UNKNOWN = 0  # anyone not in the user list
    USER = 1
    ADMIN = 2
    OWNER = 3  # only the user given at bot start

    @property
    def label(self) -> str:
        return self.name.lower()


# Roles an admin may hand out. UNKNOWN means "not a user" (use /users remove); OWNER is fixed at bot start.
ASSIGNABLE_ROLES = (Role.USER, Role.ADMIN)
# Handling of social network links needs at least this role.
SOCIAL_LINKS_MIN_ROLE = Role.USER


def ParseRole(text: str) -> Role:
    for role in Role:
        if role.label == text.strip().lower():
            return role
    raise UserError(f"Unknown role '{text}'. Roles: {', '.join(r.label for r in ASSIGNABLE_ROLES)}.")


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
    role: Role = Role.USER

    def ToDict(self) -> dict:
        return {"name": self.name, "telegramId": self.telegramId, "username": self.username, "role": self.role.label}

    @staticmethod
    def FromDict(data: dict) -> User:
        if data.get("role"):
            role = ParseRole(data["role"])
        elif data.get("owner"):  # file written before roles existed
            role = Role.OWNER
        elif "admin" in (data.get("permissions") or []):  # an explicit admin permission; "*" was only the default
            role = Role.ADMIN
        else:
            role = Role.USER  # never gain privileges in a migration
        return User(name=data["name"], telegramId=data.get("telegramId"), username=data.get("username"), role=role)

    def Describe(self) -> str:
        ident = []
        if self.telegramId is not None:
            ident.append(f"id {self.telegramId}")
        elif self.username:
            ident.append("id not linked yet")
        if self.username:
            ident.append(f"@{self.username}")
        return f"{self.name} ({', '.join(ident)}) [{self.role.label}]"


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
        """The owner: the user with the owner role (set at bot start by EnsureInitialUser)."""
        with self.lock:
            return next((user for user in self.users if user.role == Role.OWNER), None)

    def DeniedText(self) -> str:
        owner = self.GetOwner()
        if owner is None:
            who = "the owner"
        else:
            who = f"@{owner.username}" if owner.username else owner.name
        return f"Please ask my owner {who} to allow me to help you too."

    # ---- mutations ---------------------------------------------------

    def _Create(self, ref: UserRef, name: str | None, role: Role) -> User:
        name = name or ref.username or str(ref.telegramId)
        if not NAME_PATTERN.match(name):
            raise UserError("Name must be 1-32 chars: letters, digits, '_', '.', '-'.")
        for existing in self.users:
            if ref.telegramId is not None and existing.telegramId == ref.telegramId:
                raise UserError(f"User with ID {ref.telegramId} already exists ({existing.name}).")
            if ref.username is not None and existing.username == ref.username:
                raise UserError(f"User @{ref.username} already exists ({existing.name}).")
            if existing.name.lower() == name.lower():
                raise UserError(f"Name '{name}' is already taken.")
        user = User(name=name, telegramId=ref.telegramId, username=ref.username, role=role)
        self.users.append(user)
        self._Save()
        return user

    @staticmethod
    def _CheckAssignable(role: Role) -> None:
        if role == Role.OWNER:
            raise UserError("The owner role belongs to the user given at bot start and cannot be assigned.")
        if role not in ASSIGNABLE_ROLES:
            raise UserError(f"Role must be one of: {', '.join(r.label for r in ASSIGNABLE_ROLES)}.")

    def Add(self, ref: UserRef, name: str | None = None, role: Role = Role.USER) -> User:
        with self.lock:
            self._CheckAssignable(role)
            return self._Create(ref, name, role)

    def UpdateRole(self, user: User, role: Role) -> User:
        with self.lock:
            if user not in self.users:
                raise UserError("No such user.")
            if user.role == Role.OWNER:
                raise UserError("The owner's role cannot be changed.")
            self._CheckAssignable(role)
            user.role = role
            self._Save()
            return user

    def Remove(self, user: User) -> None:
        with self.lock:
            if user not in self.users:
                raise UserError("No such user.")
            if user.role == Role.OWNER:
                raise UserError("The owner cannot be removed.")
            self.users.remove(user)
            self._Save()

    def EnsureInitialUser(self, reference: str) -> User:
        """Make the user given at bot start the one and only owner, creating them if needed.

        Anyone else who held the owner role (e.g. the start user changed) becomes an admin.
        """
        with self.lock:
            ref = ParseUserRef(reference)
            target = next(
                (
                    user
                    for user in self.users
                    if (ref.telegramId is not None and user.telegramId == ref.telegramId)
                    or (ref.username is not None and user.username == ref.username)
                ),
                None,
            )
            if target is None:
                name = "owner"
                while any(u.name.lower() == name for u in self.users):
                    name += "_"
                target = self._Create(ref, name, Role.OWNER)
            changed = target.role != Role.OWNER
            target.role = Role.OWNER
            for user in self.users:
                if user is not target and user.role == Role.OWNER:
                    user.role, changed = Role.ADMIN, True
            if changed:
                self._Save()
            return target

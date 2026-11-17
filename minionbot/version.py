"""Which version of the bot is running: the commit written into the release by the deployment workflow."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

VERSION_FILE = Path(__file__).with_name("version.json")


def DescribeVersion(path: Path = VERSION_FILE) -> str:
    """'abc1234, 2026-11-17 01:03 UTC' for a deployed release, 'development (no version file)' otherwise. Never raises."""
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        commit = str(info.get("commit", ""))[:7]
        if not commit:
            raise ValueError("no commit")
    except (OSError, ValueError, AttributeError):
        return "development (no version file)"
    text = commit
    try:
        stamp = datetime.fromisoformat(str(info["date"]))
        text += f", {stamp.astimezone().strftime('%Y-%m-%d %H:%M %Z')}"
    except (KeyError, ValueError):
        pass
    return text

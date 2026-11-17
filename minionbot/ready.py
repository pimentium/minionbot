"""Tell a supervisor that the bot is up: write a small file once Telegram accepted the token.

Used by the self-updating container: after a deployment it waits for this file (newer than the process start) before it
keeps the new version. Without READY_FILE nothing is written.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def WriteReadyFile(path: str) -> bool:
    """Atomically write the current time to path. Returns False (and logs) when it can't be written; never raises."""
    if not path:
        return False
    target = Path(path)
    temp = target.with_name(f"{target.name}.tmp{os.getpid()}")
    try:
        temp.write_text(f"{time.time():.3f}\n", encoding="utf-8")
        os.replace(temp, target)
    except OSError as error:
        LOGGER.warning("Could not write the ready file %s: %s", path, error)
        try:
            temp.unlink()
        except OSError:
            pass
        return False
    return True

"""Keep the bot alive: rerun it after a crash, with exponential backoff."""

from __future__ import annotations

import logging
import time
from typing import Callable

LOGGER = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 300
STABLE_SECONDS = 60


def RunForever(
    runOnce: Callable[[int, str], None],
    isFatal: Callable[[BaseException], bool] = lambda error: False,
    scrub: Callable[[str], str] = lambda text: text,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    maxBackoff: float = MAX_BACKOFF_SECONDS,
    stableSeconds: float = STABLE_SECONDS,
) -> None:
    """Call runOnce(restartCount, lastError) until it returns normally (a deliberate stop).

    An exception means a crash: log it, wait (2, 4, 8 ... up to maxBackoff seconds; the counter resets
    after a run that lasted stableSeconds) and run again. Fatal errors (e.g. an invalid token) and
    KeyboardInterrupt/SystemExit are not retried.
    """
    restartCount = 0
    failures = 0
    lastError = ""
    while True:
        startedAt = clock()
        try:
            runOnce(restartCount, lastError)
            return
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            if isFatal(error):
                LOGGER.critical("Fatal error, not restarting: %s: %s", type(error).__name__, scrub(str(error)))
                raise
            lastError = scrub(f"{type(error).__name__}: {error}")[:300]
            if clock() - startedAt >= stableSeconds:
                failures = 0
            failures += 1
            delay = min(maxBackoff, 2**failures)
            LOGGER.error("Bot crashed (%s). Restarting in %.0f s (restart #%d)", lastError, delay, restartCount + 1, exc_info=True)
            sleep(delay)
            restartCount += 1

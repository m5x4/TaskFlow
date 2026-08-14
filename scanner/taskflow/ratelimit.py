"""Request pacing.

Gemini's free tier enforces a per-minute request quota, and the scanner's
natural shape — loop over emails as fast as Gmail returns them — trips it
immediately. Staying under the limit is strictly better than retrying after
hitting it: a retry that runs into the same per-minute quota still consumes
daily quota, and buys nothing.

`clock` and `sleep` are injectable so the tests can assert on spacing without
actually sleeping.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger(__name__)


class RateLimiter:
    """Spaces calls to at most `per_minute` requests.

    Only the gap since the previous call matters, so a caller that is already
    slower than the limit is never delayed — the pacing cost is zero whenever
    the work itself takes longer than the interval.
    """

    def __init__(
        self,
        per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # Non-positive disables pacing entirely — the escape hatch for a paid
        # key with no meaningful per-minute limit.
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._last_call: float | None = None
        self.total_waited = 0.0

    @property
    def enabled(self) -> bool:
        return self.interval > 0

    def wait(self) -> float:
        """Block until the next call is allowed. Returns seconds slept."""
        now = self._clock()

        if not self.enabled or self._last_call is None:
            self._last_call = now
            return 0.0

        elapsed = now - self._last_call
        delay = self.interval - elapsed

        if delay <= 0:
            self._last_call = now
            return 0.0

        log.debug("Pacing: sleeping %.1fs to stay under the rate limit", delay)
        self._sleep(delay)
        self.total_waited += delay
        # Advance from the intended slot rather than re-reading the clock, so
        # rounding in sleep() cannot accumulate drift across a long scan.
        self._last_call = now + delay
        return delay

    def estimate_seconds(self, calls: int) -> float:
        """Roughly how long `calls` requests will take, for an ETA.

        The first call is never delayed, hence calls - 1.
        """
        if not self.enabled or calls <= 1:
            return 0.0
        return (calls - 1) * self.interval

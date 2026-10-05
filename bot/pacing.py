"""Edit pacing for streamed Telegram replies.

Pure logic, no Telegram imports, so it is unit-testable without a token.

Telegram rate-limits edits per chat (HTTP 429, `retry_after` seconds). The
Bot API FAQ asks bots to stay under about one message per second in a single
chat, and exact edit limits are not published, so the pacer does two things:
it starts above that guideline, and when Telegram does push back it honours
`retry_after` WITHOUT sleeping (the caller keeps reading the model stream and
simply skips edits until the cooldown ends) and widens the interval so the
same limit is not hit again on the next reply.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class EditPacer:
    def __init__(
        self,
        base: float,
        *,
        cap: float = 6.0,
        floor: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = max(0.0, base)
        self._cap = max(cap, self._base)
        self._floor = floor  # smallest interval used after the first 429
        self._clock = clock
        self.interval = self._base
        self._next_at = 0.0

    def ready(self) -> bool:
        """True when an edit may be sent now."""
        return self._clock() >= self._next_at

    def wait(self) -> float:
        """Seconds until the next edit may be sent (0 if ready)."""
        return max(0.0, self._next_at - self._clock())

    def sent(self) -> None:
        """Record a successful edit: the next one waits one interval."""
        self._next_at = max(self._next_at, self._clock() + self.interval)

    def flood(self, retry_after: float) -> None:
        """Record a 429: honour `retry_after` and slow down (x2, capped)."""
        self.interval = min(self._cap, max(self.interval * 2, self._floor))
        self._next_at = max(self._next_at, self._clock() + max(0.0, retry_after))

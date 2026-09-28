"""Idle-shutdown policy for the bot (ROADMAP Phase 6).

Pure logic: no Telegram, no env vars, injectable clock. That keeps it
unit-testable without a bot token and keeps bot.py to a thin adapter.

The clock is monotonic on purpose. Wall-clock time can jump (NTP sync on a
fresh runner), and a jump backwards would silently extend the session while
a jump forwards would kill it early.
"""

from __future__ import annotations

import enum
import time
from collections.abc import Callable


class IdleAction(enum.Enum):
    NONE = "none"
    WARN = "warn"  # emitted at most once per idle stretch
    STOP = "stop"


class IdleTracker:
    """Tracks time since the owner last did something.

    timeout_s == 0 disables idle shutdown entirely (check() always NONE).
    warn_before_s is how long before the timeout to emit a single WARN; a
    value <= 0 or >= timeout_s disables the warning (otherwise it would fire
    immediately at boot, which is just noise).
    """

    def __init__(
        self,
        timeout_s: float,
        warn_before_s: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_s < 0:
            raise ValueError("timeout_s must be >= 0")
        self._timeout = timeout_s
        self._warn_at: float | None = (
            timeout_s - warn_before_s if 0 < warn_before_s < timeout_s else None
        )
        self._clock = clock
        self._last = clock()
        self._warned = False

    def touch(self) -> None:
        """Record owner activity. Re-arms the warning for the next idle stretch."""
        self._last = self._clock()
        self._warned = False

    def idle_seconds(self) -> float:
        return self._clock() - self._last

    def check(self) -> IdleAction:
        if self._timeout == 0:
            return IdleAction.NONE
        idle = self.idle_seconds()
        if idle >= self._timeout:
            return IdleAction.STOP
        if self._warn_at is not None and idle >= self._warn_at and not self._warned:
            self._warned = True
            return IdleAction.WARN
        return IdleAction.NONE

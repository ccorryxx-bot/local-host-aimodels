"""Run from the bot/ directory:  python -m unittest test_idle -v"""

import unittest

from idle import IdleAction, IdleTracker


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class IdleTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        # 20 min timeout, warn 5 min before (i.e. at 15 min idle).
        self.t = IdleTracker(1200, 300, clock=self.clock)

    def test_nothing_before_warn_threshold(self) -> None:
        self.clock.advance(899)
        self.assertIs(self.t.check(), IdleAction.NONE)

    def test_warn_fires_once(self) -> None:
        self.clock.advance(900)
        self.assertIs(self.t.check(), IdleAction.WARN)
        self.clock.advance(60)
        self.assertIs(self.t.check(), IdleAction.NONE)

    def test_stop_at_timeout(self) -> None:
        self.clock.advance(1200)
        self.assertIs(self.t.check(), IdleAction.STOP)

    def test_stop_even_if_warn_was_skipped(self) -> None:
        # Watchdog stalled past both thresholds: STOP wins, no late WARN.
        self.clock.advance(5000)
        self.assertIs(self.t.check(), IdleAction.STOP)

    def test_touch_resets_and_rearms_warning(self) -> None:
        self.clock.advance(900)
        self.assertIs(self.t.check(), IdleAction.WARN)
        self.t.touch()
        self.clock.advance(899)
        self.assertIs(self.t.check(), IdleAction.NONE)
        self.clock.advance(1)
        self.assertIs(self.t.check(), IdleAction.WARN)

    def test_timeout_zero_disables(self) -> None:
        t = IdleTracker(0, 300, clock=self.clock)
        self.clock.advance(10**6)
        self.assertIs(t.check(), IdleAction.NONE)

    def test_warning_disabled_when_window_not_smaller_than_timeout(self) -> None:
        t = IdleTracker(300, 300, clock=self.clock)
        self.assertIs(t.check(), IdleAction.NONE)
        self.clock.advance(299)
        self.assertIs(t.check(), IdleAction.NONE)
        self.clock.advance(1)
        self.assertIs(t.check(), IdleAction.STOP)

    def test_negative_timeout_rejected(self) -> None:
        with self.assertRaises(ValueError):
            IdleTracker(-1)


if __name__ == "__main__":
    unittest.main()

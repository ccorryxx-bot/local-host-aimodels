"""Unit tests for pacing.EditPacer (fake clock, no sleeping).

Run from the bot/ directory:  python -m unittest test_pacing -v
"""

import unittest

from pacing import EditPacer


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class EditPacerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()

    def pacer(self, base: float = 1.2, **kw) -> EditPacer:
        return EditPacer(base, clock=self.clock, **kw)

    def test_ready_at_start(self) -> None:
        self.assertTrue(self.pacer().ready())

    def test_sent_blocks_until_interval_passes(self) -> None:
        p = self.pacer()
        p.sent()
        self.assertFalse(p.ready())
        self.clock.now += 1.19
        self.assertFalse(p.ready())
        self.clock.now += 0.02
        self.assertTrue(p.ready())

    def test_flood_honours_retry_after_without_sleeping(self) -> None:
        p = self.pacer()
        p.flood(12)
        self.assertFalse(p.ready())
        self.assertAlmostEqual(p.wait(), 12.0)
        self.clock.now += 12
        self.assertTrue(p.ready())
        self.assertEqual(p.wait(), 0.0)

    def test_flood_widens_interval_and_caps_it(self) -> None:
        p = self.pacer(1.2, cap=6.0)
        seen = []
        for _ in range(5):
            p.flood(0)
            seen.append(round(p.interval, 2))
        self.assertEqual(seen, [2.4, 4.8, 6.0, 6.0, 6.0])

    def test_zero_base_gets_a_floor_after_first_flood(self) -> None:
        p = self.pacer(0.0)
        self.assertEqual(p.interval, 0.0)
        p.flood(0)
        self.assertEqual(p.interval, 1.0)

    def test_later_edit_never_shortens_a_cooldown(self) -> None:
        p = self.pacer()
        p.flood(10)
        p.sent()  # e.g. a forced edit sneaking in must not cut the cooldown short
        self.assertAlmostEqual(p.wait(), 10.0)


if __name__ == "__main__":
    unittest.main()

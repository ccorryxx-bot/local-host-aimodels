"""Run from the bot/ directory:  python -m unittest test_chunking -v"""

import unittest

from chunking import split_point


class SplitPointTests(unittest.TestCase):
    def test_short_text_is_not_cut(self) -> None:
        self.assertEqual(split_point("hello", 100), 5)

    def test_prefers_newline_in_tail_of_window(self) -> None:
        text = "a" * 80 + "\n" + "b" * 50
        self.assertEqual(split_point(text, 100), 81)

    def test_ignores_newline_too_early_in_window(self) -> None:
        # Newline at index 10 is outside the last 30% of a 100 window.
        text = "a" * 10 + "\n" + "b" * 200
        self.assertEqual(split_point(text, 100), 100)

    def test_falls_back_to_space(self) -> None:
        text = "a" * 85 + " " + "b" * 50
        self.assertEqual(split_point(text, 100), 86)

    def test_hard_cut_when_no_break(self) -> None:
        self.assertEqual(split_point("x" * 500, 100), 100)

    def test_never_strands_a_combining_mark(self) -> None:
        # U+103C (medial ra) is a spacing combining mark; it must stay
        # attached to the base letter before it.
        # The mark sits exactly at index == limit, i.e. the first char of the
        # next chunk: the cut must back up so its base letter travels with it.
        text = "က" * 10 + "\u103c" + "က" * 20
        cut = split_point(text, 10)
        self.assertEqual(cut, 9)
        self.assertNotEqual(text[cut], "\u103c")

    def test_chunks_reassemble_losslessly(self) -> None:
        text = ("line of code\n" * 300) + ("မြန်မာစာ " * 200)
        parts, rest = [], text
        while rest:
            i = split_point(rest, 500)
            self.assertLessEqual(i, 500)
            parts.append(rest[:i])
            rest = rest[i:]
        self.assertEqual("".join(parts), text)

    def test_bad_limit_rejected(self) -> None:
        with self.assertRaises(ValueError):
            split_point("abc", 0)


if __name__ == "__main__":
    unittest.main()

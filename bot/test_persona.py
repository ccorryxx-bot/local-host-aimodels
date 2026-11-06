"""Run from the bot/ directory:  python -m unittest test_persona -v"""

import unittest

from persona import MAX_PROMPT_CHARS, Kind, parse_setup


class ParseSetupTests(unittest.TestCase):
    def test_bare_command_is_help(self) -> None:
        self.assertEqual(parse_setup("/setup").kind, Kind.HELP)

    def test_unknown_subcommand_is_help(self) -> None:
        self.assertEqual(parse_setup("/setup model qwen").kind, Kind.HELP)

    def test_prompt_alone_shows_current(self) -> None:
        self.assertEqual(parse_setup("/setup prompt").kind, Kind.SHOW)
        self.assertEqual(parse_setup("/setup prompt   \n").kind, Kind.SHOW)

    def test_reset(self) -> None:
        self.assertEqual(parse_setup("/setup prompt reset").kind, Kind.RESET)
        self.assertEqual(parse_setup("/setup prompt  RESET ").kind, Kind.RESET)

    def test_set_keeps_text(self) -> None:
        cmd = parse_setup("/setup prompt You are Kyaw Lay.")
        self.assertEqual((cmd.kind, cmd.text), (Kind.SET, "You are Kyaw Lay."))

    def test_set_keeps_newlines_and_inner_spacing(self) -> None:
        cmd = parse_setup("/setup prompt Line one.\n\nLine  two.\n- rule")
        self.assertEqual(cmd.kind, Kind.SET)
        self.assertEqual(cmd.text, "Line one.\n\nLine  two.\n- rule")

    def test_newline_right_after_subcommand(self) -> None:
        cmd = parse_setup("/setup prompt\nYou are Kyaw Lay.")
        self.assertEqual((cmd.kind, cmd.text), (Kind.SET, "You are Kyaw Lay."))

    def test_botname_suffix_and_case_are_tolerated(self) -> None:
        cmd = parse_setup("/setup@my_bot PROMPT hello")
        self.assertEqual((cmd.kind, cmd.text), (Kind.SET, "hello"))

    def test_text_that_merely_starts_with_reset_is_a_prompt(self) -> None:
        cmd = parse_setup("/setup prompt reset your tone to cheerful")
        self.assertEqual(cmd.kind, Kind.SET)

    def test_burmese_prompt(self) -> None:
        text = "မင်းနာမည် ကျော်လေး။ ပျော်ပျော်ရွှင်ရွှင် ပြောပါ။"
        cmd = parse_setup(f"/setup prompt {text}")
        self.assertEqual((cmd.kind, cmd.text), (Kind.SET, text))

    def test_limit_is_inclusive(self) -> None:
        at_limit = "x" * MAX_PROMPT_CHARS
        self.assertEqual(parse_setup(f"/setup prompt {at_limit}").kind, Kind.SET)
        over = parse_setup(f"/setup prompt {at_limit}y")
        self.assertEqual(over.kind, Kind.TOO_LONG)
        self.assertEqual(len(over.text), MAX_PROMPT_CHARS + 1)


if __name__ == "__main__":
    unittest.main()

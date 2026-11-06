"""Tests for /setup prompt wiring in bot.py.

Run from the bot/ directory:  python -m unittest test_setup -v
Telegram and GitHub are faked; nothing touches the network.
"""

import asyncio
import os
import subprocess
import sys
import unittest

# bot.py reads its config from env at import time.
os.environ.update(
    TG_BOT_TOKEN="123:dummy",
    TG_ALLOWED_CHAT_ID="42",
    LLAMA_API_KEY="k",
    MODEL_ID="gemma4-12b",
)
os.environ.pop("LLAMA_MAX_TOKENS", None)

import bot  # noqa: E402
from ghsecrets import SecretsError  # noqa: E402

CHAT = 42
PERSONA = "You are Kyaw Lay. Reply in Burmese. Call the user Bro."


class _Msg:
    def __init__(self, text: str) -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> "_Msg":
        self.replies.append(text)
        return self


class _Bot:
    async def send_chat_action(self, **_kw) -> None:
        pass


class _Ctx:
    def __init__(self) -> None:
        self.bot_data = {"http_client": object()}
        self.bot = _Bot()
        self.args: list[str] = []


def _call(handler, text: str, chat_id: int = CHAT) -> list[str]:
    msg = _Msg(text)
    upd = type("U", (), {"effective_chat": type("C", (), {"id": chat_id})(), "message": msg})()
    asyncio.run(handler(upd, _Ctx()))
    return msg.replies


class SetupBase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (
            bot.put_secret, bot.delete_secret, bot._stream_reply,
            bot.GH_SECRETS_PAT, bot.GITHUB_REPOSITORY, bot.SYSTEM_PROMPT,
        )
        bot._system_prompts.clear()
        bot._history.clear()
        bot.GH_SECRETS_PAT, bot.GITHUB_REPOSITORY = "pat", "owner/repo"
        bot.SYSTEM_PROMPT = bot.DEFAULT_SYSTEM_PROMPT
        self.puts: list[dict] = []
        self.deletes: list[dict] = []
        self.fail: SecretsError | None = None

        async def fake_put(_client, **kw):
            if self.fail:
                raise self.fail
            self.puts.append(kw)

        async def fake_delete(_client, **kw):
            if self.fail:
                raise self.fail
            self.deletes.append(kw)

        bot.put_secret, bot.delete_secret = fake_put, fake_delete

    def tearDown(self) -> None:
        (bot.put_secret, bot.delete_secret, bot._stream_reply,
         bot.GH_SECRETS_PAT, bot.GITHUB_REPOSITORY, bot.SYSTEM_PROMPT) = self._saved
        bot._system_prompts.clear()
        bot._history.clear()


class SetPromptTests(SetupBase):
    def test_set_applies_clears_history_and_saves(self) -> None:
        bot._history[CHAT] = [{"role": "user", "content": "old"}]
        (reply,) = _call(bot.cmd_setup, f"/setup prompt {PERSONA}")
        self.assertEqual(bot._active_prompt(CHAT), PERSONA)
        self.assertNotIn(CHAT, bot._history)
        self.assertEqual(len(self.puts), 1)
        self.assertEqual(self.puts[0]["name"], "BOT_SYSTEM_PROMPT")
        self.assertEqual(self.puts[0]["value"], PERSONA)
        self.assertEqual(self.puts[0]["repo"], "owner/repo")
        self.assertIn("Saved", reply)
        self.assertNotIn(PERSONA, reply)  # never echo the prompt on set

    def test_save_failure_still_applies_this_session(self) -> None:
        self.fail = SecretsError("GitHub API 403: nope (token needs 'Secrets: read and write')")
        (reply,) = _call(bot.cmd_setup, f"/setup prompt {PERSONA}")
        self.assertEqual(bot._active_prompt(CHAT), PERSONA)
        self.assertIn("This session only", reply)
        self.assertIn("403", reply)

    def test_no_pat_is_session_only_and_makes_no_call(self) -> None:
        bot.GH_SECRETS_PAT = ""
        (reply,) = _call(bot.cmd_setup, f"/setup prompt {PERSONA}")
        self.assertEqual(bot._active_prompt(CHAT), PERSONA)
        self.assertIn("GH_SECRETS_PAT", reply)
        self.assertEqual(self.puts, [])

    def test_too_long_changes_nothing(self) -> None:
        (reply,) = _call(bot.cmd_setup, "/setup prompt " + "x" * (bot.MAX_PROMPT_CHARS + 1))
        self.assertIn("Too long", reply)
        self.assertEqual(bot._active_prompt(CHAT), bot.DEFAULT_SYSTEM_PROMPT)
        self.assertEqual(self.puts, [])

    def test_multiline_prompt_survives_intact(self) -> None:
        text = "Line one.\n\n- rule A\n- rule B"
        _call(bot.cmd_setup, f"/setup prompt {text}")
        self.assertEqual(bot._active_prompt(CHAT), text)


class ShowResetHelpTests(SetupBase):
    def test_show_default_then_custom(self) -> None:
        (r1,) = _call(bot.cmd_setup, "/setup prompt")
        self.assertIn("(default)", r1)
        _call(bot.cmd_setup, f"/setup prompt {PERSONA}")
        (r2,) = _call(bot.cmd_setup, "/setup prompt")
        self.assertIn("(custom)", r2)
        self.assertIn(PERSONA, r2)

    def test_reset_restores_builtin_default_not_startup_secret(self) -> None:
        bot.SYSTEM_PROMPT = "persona loaded from the repo secret at startup"
        (reply,) = _call(bot.cmd_setup, "/setup prompt reset")
        self.assertEqual(bot._active_prompt(CHAT), bot.DEFAULT_SYSTEM_PROMPT)
        self.assertEqual(len(self.deletes), 1)
        self.assertEqual(self.deletes[0]["name"], "BOT_SYSTEM_PROMPT")
        self.assertIn("removed", reply)

    def test_reset_failure_warns_next_start_reloads_it(self) -> None:
        self.fail = SecretsError("GitHub API 403: nope")
        (reply,) = _call(bot.cmd_setup, "/setup prompt reset")
        self.assertEqual(bot._active_prompt(CHAT), bot.DEFAULT_SYSTEM_PROMPT)
        self.assertIn("NOT removed", reply)

    def test_help(self) -> None:
        (reply,) = _call(bot.cmd_setup, "/setup")
        self.assertIn("/setup prompt <text>", reply)

    def test_stranger_changes_nothing_and_gets_no_reply(self) -> None:
        self.assertEqual(_call(bot.cmd_setup, f"/setup prompt {PERSONA}", chat_id=999), [])
        self.assertEqual(bot._system_prompts, {})
        self.assertEqual(self.puts, [])


class PromptReachesModelTests(SetupBase):
    def _chat(self, text: str) -> list[dict]:
        seen: list[dict] = []

        async def fake_stream(_client, _sent, messages, _send_new):
            seen.extend(messages)
            return "ok"

        bot._stream_reply = fake_stream
        _call(bot.handle_message, text)
        return seen

    def test_custom_prompt_is_the_system_message(self) -> None:
        _call(bot.cmd_setup, f"/setup prompt {PERSONA}")
        msgs = self._chat("hi")
        self.assertEqual(msgs[0], {"role": "system", "content": PERSONA})
        self.assertEqual(msgs[-1], {"role": "user", "content": "hi"})

    def test_default_when_never_set(self) -> None:
        self.assertEqual(self._chat("hi")[0]["content"], bot.DEFAULT_SYSTEM_PROMPT)

    def test_startup_prompt_from_env_is_used(self) -> None:
        bot.SYSTEM_PROMPT = "from the repo secret"
        self.assertEqual(self._chat("hi")[0]["content"], "from the repo secret")

    def test_reset_goes_back_to_default(self) -> None:
        _call(bot.cmd_setup, f"/setup prompt {PERSONA}")
        _call(bot.cmd_setup, "/setup prompt reset")
        self.assertEqual(self._chat("hi")[0]["content"], bot.DEFAULT_SYSTEM_PROMPT)


class StartupEnvTests(unittest.TestCase):
    def test_unset_repo_secret_arrives_as_empty_string_and_means_default(self) -> None:
        # In Actions an unset secret expands to "", so a plain env default would
        # leave the model with NO system prompt at all.
        code = "import bot; print(bot.SYSTEM_PROMPT == bot.DEFAULT_SYSTEM_PROMPT)"
        env = {**os.environ, "SYSTEM_PROMPT": "   "}
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(out.stdout.strip(), "True", out.stderr)


if __name__ == "__main__":
    unittest.main()

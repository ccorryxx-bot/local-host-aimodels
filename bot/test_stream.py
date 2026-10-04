"""Integration tests for bot.py's streaming/rollover and history logic.

Run from the bot/ directory:  python -m unittest test_stream -v
Needs python-telegram-bot + httpx installed (bot/requirements.txt); no token,
network or llama-server: Telegram messages and stream_chat are faked.
"""

import asyncio
import os
import unittest

import httpx

# bot.py reads its config from env at import time.
os.environ.update(
    TG_BOT_TOKEN="123:dummy",
    TG_ALLOWED_CHAT_ID="42",
    LLAMA_API_KEY="k",
    MODEL_ID="gemma4-12b",
)
os.environ.pop("LLAMA_MAX_TOKENS", None)

import bot  # noqa: E402
from llm import LlamaError  # noqa: E402

bot.EDIT_INTERVAL = 0  # no throttle in tests


class FakeMessage:
    """Records edits; rejects what Telegram would reject."""

    def __init__(self, text: str = "...", reject_html: bool = False) -> None:
        self.text = text
        self.edits: list[str] = []
        self.reject_html = reject_html

    async def edit_text(self, text: str, parse_mode: str | None = None) -> None:
        if not text.strip():
            raise AssertionError("Telegram would reject empty text")
        if len(text) > 4096:
            raise AssertionError(f"message too long: {len(text)}")
        if self.reject_html and parse_mode:
            from telegram.error import BadRequest

            raise BadRequest("Can't parse entities: unsupported start tag")
        self.text = text
        self.edits.append(text)


def _fake_stream(deltas: list[str], fail: Exception | None = None):
    async def gen(*_a, **_kw):
        for d in deltas:
            yield d
        if fail:
            raise fail

    return gen


async def _run(deltas, fail=None):
    sent = FakeMessage()
    made = [sent]

    async def send_new(text: str) -> FakeMessage:
        m = FakeMessage(text)
        made.append(m)
        return m

    bot.stream_chat = _fake_stream(deltas, fail)
    reply = await bot._stream_reply(None, sent, [], send_new)
    return reply, made


class StreamRolloverTests(unittest.TestCase):
    def test_long_reply_arrives_whole_across_messages(self) -> None:
        code = "".join(f"def f{i}(x):\n    return x + {i}\n\n" for i in range(600))
        burmese = "မြန်မာစာ စမ်းသပ်ခြင်း။ " * 300
        full = code + burmese
        deltas = [full[i : i + 37] for i in range(0, len(full), 37)]
        reply, made = asyncio.run(_run(deltas))
        self.assertEqual(reply, full)
        self.assertGreaterEqual(len(made), 3)
        self.assertEqual("".join(m.text for m in made), full)
        self.assertTrue(all(len(m.text) <= bot.CHUNK for m in made))

    def test_short_reply_uses_one_message(self) -> None:
        reply, made = asyncio.run(_run(["Hel", "lo"]))
        self.assertEqual(reply, "Hello")
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0].text, "Hello")

    def test_error_suffix_shown_but_not_returned(self) -> None:
        reply, made = asyncio.run(_run(["partial"], fail=LlamaError("boom")))
        self.assertEqual(reply, "partial")  # history must not get the suffix
        self.assertIn("[error: boom]", made[-1].text)

    def test_error_before_any_text(self) -> None:
        reply, made = asyncio.run(_run([], fail=LlamaError("boom")))
        self.assertEqual(reply, "")
        self.assertEqual(made[0].text, "Error: boom")

    def test_connection_error_names_type_and_keeps_partial_text(self) -> None:
        # client is None in these tests, so the health probe itself fails and
        # must be reported as "down" rather than masking the original error.
        reply, made = asyncio.run(_run(["partial"], fail=httpx.ReadTimeout("")))
        self.assertEqual(reply, "partial")
        self.assertIn("[connection error: ReadTimeout, server down]", made[-1].text)

    def test_connection_error_before_any_text(self) -> None:
        reply, made = asyncio.run(_run([], fail=httpx.RemoteProtocolError("closed")))
        self.assertEqual(reply, "")
        self.assertEqual(made[0].text, "Connection error: RemoteProtocolError, server down.")

    def test_code_block_is_sent_as_pre_with_html_parse_mode(self) -> None:
        reply, made = asyncio.run(_run(["Try:\n```py", "thon\nprint(1)\n```\n", "Done."]))
        self.assertEqual(reply, "Try:\n```python\nprint(1)\n```\nDone.")  # history stays raw
        self.assertIn('<pre><code class="language-python">print(1)</code></pre>', made[0].text)

    def test_code_block_split_across_messages_reopens_in_next(self) -> None:
        code = "".join(f"x{i} = {i}\n" for i in range(900))  # > CHUNK, one block
        reply, made = asyncio.run(_run(["```python\n", code, "```\nEnd."]))
        self.assertGreater(len(made), 1)
        for m in made[:-1]:
            self.assertIn("<pre>", m.text)
            self.assertIn("</pre>", m.text)
        self.assertIn("<pre>", made[-1].text)  # the continuation is still monospace
        self.assertTrue(made[-1].text.rstrip().endswith("End."))

    def test_falls_back_to_plain_text_if_telegram_rejects_html(self) -> None:
        async def go():
            sent = FakeMessage(reject_html=True)

            async def send_new(text: str) -> FakeMessage:
                return FakeMessage(text, reject_html=True)

            bot.stream_chat = _fake_stream(["```python\nprint(1)\n```"])
            return await bot._stream_reply(None, sent, [], send_new), sent

        reply, sent = asyncio.run(go())
        self.assertEqual(reply, "```python\nprint(1)\n```")
        self.assertEqual(sent.text, "```python\nprint(1)\n```")  # plain, nothing lost


class ModelConfigTests(unittest.TestCase):
    def test_gemma_settings_applied(self) -> None:
        self.assertEqual(bot.MODEL_ID, "gemma4-12b")
        self.assertEqual(bot.MAX_TOKENS, 1500)
        self.assertEqual(bot.SAMPLING["top_k"], 64)
        # (8192 - 1500) * 2 chars
        self.assertEqual(bot.HISTORY_CHAR_BUDGET, 13384)


class TrimHistoryTests(unittest.TestCase):
    @staticmethod
    def _hist(n_pairs: int, size: int) -> list[dict[str, str]]:
        h: list[dict[str, str]] = []
        for i in range(n_pairs):
            h.append({"role": "user", "content": f"u{i}" + "x" * size})
            h.append({"role": "assistant", "content": f"a{i}" + "y" * size})
        return h

    def test_char_budget_drops_whole_pairs_from_front(self) -> None:
        h = self._hist(6, 1000) + [{"role": "user", "content": "now"}]
        bot._trim_history(h, max_turns=12, max_chars=5000)
        self.assertEqual(h[0]["role"], "user")  # still starts with a user turn
        self.assertEqual(h[-1]["content"], "now")  # newest message survives
        self.assertEqual(len(h) % 2, 1)  # u,a,u,a,...,u
        self.assertLessEqual(sum(len(m["content"]) for m in h), 5000)

    def test_huge_single_message_is_kept(self) -> None:
        h = [{"role": "user", "content": "z" * 50000}]
        bot._trim_history(h, max_turns=12, max_chars=1000)
        self.assertEqual(len(h), 1)

    def test_turn_limit_keeps_pairs_plus_pending_user_turn(self) -> None:
        # Regression: this used to leave an assistant turn at the front, which
        # strict chat templates (Gemma) reject after ~12 turns.
        h = self._hist(20, 1) + [{"role": "user", "content": "q"}]
        bot._trim_history(h, max_turns=3, max_chars=10**9)
        self.assertEqual(len(h), 7)  # 3 pairs + the pending user message
        self.assertEqual(h[0]["role"], "user")
        self.assertEqual(h[-1]["content"], "q")

    def test_turn_limit_after_reply_is_whole_pairs(self) -> None:
        h = self._hist(20, 1)
        bot._trim_history(h, max_turns=3, max_chars=10**9)
        self.assertEqual(len(h), 6)
        self.assertEqual(h[0]["role"], "user")
        self.assertEqual(h[-1]["role"], "assistant")


class CommandTests(unittest.TestCase):
    class _Chat:
        def __init__(self, cid: int) -> None:
            self.id = cid

    class _Msg:
        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, text: str) -> None:
            self.replies.append(text)

    class _Ctx:
        def __init__(self, args: list[str]) -> None:
            self.args = args

    def _call(self, handler, args=None, chat_id=42) -> list[str]:
        msg = self._Msg()
        upd = type("U", (), {"effective_chat": self._Chat(chat_id), "message": msg})()
        asyncio.run(handler(upd, self._Ctx(args or [])))
        return msg.replies

    def test_start_same_model(self) -> None:
        (r,) = self._call(bot.cmd_start, ["gemma4-12b"])
        self.assertIn("Already running", r)

    def test_start_other_model_explains_switch(self) -> None:
        (r,) = self._call(bot.cmd_start, ["padauk"])
        self.assertIn("/stop, then /start padauk", r)

    def test_model_lists_current_and_others(self) -> None:
        (r,) = self._call(bot.cmd_model)
        self.assertIn("[gemma4-12b]", r)
        self.assertIn("padauk", r)
        self.assertIn("qwen2.5-7b", r)

    def test_stranger_gets_no_reply(self) -> None:
        self.assertEqual(self._call(bot.cmd_model, chat_id=999), [])


if __name__ == "__main__":
    unittest.main()

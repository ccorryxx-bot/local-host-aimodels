"""Tests for tgformat.py (run from bot/: python -m unittest test_tgformat -v)."""

import unittest

from tgformat import to_telegram_html


class FenceTests(unittest.TestCase):
    def test_fenced_block_with_language(self) -> None:
        html, state = to_telegram_html("Run:\n```python\nprint(1)\n```\nDone.")
        self.assertEqual(
            html,
            'Run:\n<pre><code class="language-python">print(1)</code></pre>\nDone.',
        )
        self.assertIsNone(state)

    def test_fenced_block_without_language(self) -> None:
        html, _ = to_telegram_html("```\nls -la\n```")
        self.assertEqual(html, "<pre>ls -la</pre>")

    def test_unclosed_fence_is_closed_and_reported_open(self) -> None:
        # What a mid-stream edit looks like.
        html, state = to_telegram_html("Here:\n```bash\nnpm install\nnpm ru")
        self.assertEqual(html, 'Here:\n<pre><code class="language-bash">npm install\nnpm ru</code></pre>')
        self.assertEqual(state, "bash")

    def test_fence_just_opened_renders_nothing_yet(self) -> None:
        html, state = to_telegram_html("Here:\n```python\n")
        self.assertEqual(html, "Here:\n")
        self.assertEqual(state, "python")

    def test_carry_in_reopens_block_in_next_message(self) -> None:
        html, state = to_telegram_html("b = 2\nc = 3\n```\nAfter.", open_lang="python")
        self.assertEqual(
            html, '<pre><code class="language-python">b = 2\nc = 3</code></pre>\nAfter.'
        )
        self.assertIsNone(state)

    def test_html_is_escaped_inside_and_outside_code(self) -> None:
        html, _ = to_telegram_html("a < b & c\n```html\n<div class=\"x\">&</div>\n```")
        self.assertEqual(
            html,
            'a &lt; b &amp; c\n<pre><code class="language-html">&lt;div class="x"&gt;&amp;&lt;/div&gt;</code></pre>',
        )

    def test_language_cannot_inject_markup(self) -> None:
        html, _ = to_telegram_html('```py"><b>x\nprint(1)\n```')
        self.assertNotIn("<b>", html)

    def test_backticks_inside_a_code_line_do_not_close_it(self) -> None:
        html, state = to_telegram_html("```md\nuse `x` here\n```")
        self.assertIn("use `x` here", html)
        self.assertIsNone(state)


class InlineTests(unittest.TestCase):
    def test_inline_code(self) -> None:
        html, _ = to_telegram_html("Use `git push` now.")
        self.assertEqual(html, "Use <code>git push</code> now.")

    def test_unmatched_backtick_stays_literal(self) -> None:
        html, _ = to_telegram_html("Use `git pu")
        self.assertEqual(html, "Use `git pu")

    def test_inline_code_is_escaped(self) -> None:
        html, _ = to_telegram_html("`a<b`")
        self.assertEqual(html, "<code>a&lt;b</code>")

    def test_burmese_text_untouched(self) -> None:
        text = "မြန်မာစာ စမ်းသပ်ခြင်း။"
        self.assertEqual(to_telegram_html(text), (text, None))


if __name__ == "__main__":
    unittest.main()

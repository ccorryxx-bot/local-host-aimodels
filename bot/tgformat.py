"""Turn model Markdown (code only) into Telegram HTML so code shows monospace.

Pure logic, no Telegram imports. Handles exactly two things:
  * fenced blocks  ```lang ... ```  ->  <pre><code class="language-lang">...</code></pre>
  * inline `code`                   ->  <code>...</code>
Everything else is HTML-escaped plain text, so stray <, > and & in model output
can never break parsing.

Streaming: the buffer is re-rendered on every edit, so it usually ends inside an
unclosed fence. That is fine -- an open block is closed in the HTML, and the
returned state says it is still open. When a long reply rolls over into a new
Telegram message, the caller passes that state back in so the next message
starts inside the same block instead of showing the rest of the code as prose.
"""

from __future__ import annotations

import html
import re

_OPEN_FENCE = re.compile(r"^[ \t]*```[ \t]*([A-Za-z0-9_+#.-]*)[^`]*$")
_CLOSE_FENCE = re.compile(r"^[ \t]*```[ \t]*$")
_INLINE_CODE = re.compile(r"(`[^`\n]+`)")


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _inline(line: str) -> str:
    parts = _INLINE_CODE.split(line)
    return "".join(
        f"<code>{_esc(p[1:-1])}</code>" if i % 2 else _esc(p) for i, p in enumerate(parts)
    )


def _pre(code_lines: list[str], lang: str) -> str:
    code = "".join(code_lines).rstrip("\n")
    if not code:
        return ""  # Telegram rejects empty entities; show nothing until code arrives
    if lang:
        return f'<pre><code class="language-{lang}">{_esc(code)}</code></pre>'
    return f"<pre>{_esc(code)}</pre>"


def to_telegram_html(text: str, open_lang: str | None = None) -> tuple[str, str | None]:
    """Convert `text` to Telegram HTML.

    `open_lang` is the fence state carried in from the previous message: None if
    the text starts outside any block, otherwise the block's language ("" if
    none). Returns (html, state_at_end) with the same meaning for state.
    """
    out: list[str] = []
    in_fence = open_lang is not None
    lang = open_lang or ""
    code_lines: list[str] = []

    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if in_fence:
            if _CLOSE_FENCE.match(bare):
                out.append(_pre(code_lines, lang))
                if line.endswith("\n"):
                    out.append("\n")
                code_lines, in_fence, lang = [], False, ""
            else:
                code_lines.append(line)
            continue
        m = _OPEN_FENCE.match(bare)
        if m:
            in_fence, lang, code_lines = True, m.group(1), []
            continue
        out.append(_inline(bare) + line[len(bare):])

    if in_fence:  # still open (streaming, or the model forgot to close it)
        out.append(_pre(code_lines, lang))
        return "".join(out), lang
    return "".join(out), None

"""Split long model replies across Telegram messages (4096-char cap each).

Pure logic, no Telegram imports, so it is unit-testable without a token.
"""

from __future__ import annotations

import unicodedata


def split_point(text: str, limit: int) -> int:
    """Return the index at which to cut `text` so text[:i] fits in `limit` chars.

    Preference order, all within the last 30% of the window so chunks stay
    reasonably full: a newline (keeps code blocks/paragraphs together), then a
    space, then a hard cut at `limit`. The cut never lands right before a
    combining mark (Burmese vowel signs, asat, ...), which would strand the
    mark at the start of the next message, detached from its base letter.
    """
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if len(text) <= limit:
        return len(text)

    lo = int(limit * 0.7)
    nl = text.rfind("\n", lo, limit)
    if nl != -1:
        return nl + 1
    sp = text.rfind(" ", lo, limit)
    if sp != -1:
        return sp + 1

    cut = limit
    while cut > 1 and unicodedata.category(text[cut])[0] == "M":
        cut -= 1
    return cut

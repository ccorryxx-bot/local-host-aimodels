"""Split long model replies across Telegram messages (4096-char cap each).

Pure logic, no Telegram imports, so it is unit-testable without a token.
"""

from __future__ import annotations

import unicodedata

# Sentence enders, tried after a newline and before a plain space. U+104B is the
# Burmese full stop; it is not followed by a space in most Burmese text, so a
# space-only fallback would cut mid-sentence far more often than needed.
_SENTENCE_ENDS = ("\u104b", ". ", "! ", "? ")

# Characters that bind to what follows them: Burmese virama (stacks the next
# consonant under the previous one), ZWJ, ZWNJ. Never cut right after these.
_JOINERS = frozenset("\u1039\u200d\u200c")


def split_point(text: str, limit: int) -> int:
    """Return the index at which to cut `text` so text[:i] fits in `limit` chars.

    Preference order, all within the last 30% of the window so chunks stay
    reasonably full: a newline (keeps code blocks/paragraphs together), then a
    sentence end, then a space, then a hard cut at `limit`. The cut never lands
    right before a combining mark (Burmese vowel signs, asat, ...) or right
    after a joiner (virama, ZWJ), which would strand part of a syllable at the
    start of the next message, detached from its base letter.
    """
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if len(text) <= limit:
        return len(text)

    lo = int(limit * 0.7)
    nl = text.rfind("\n", lo, limit)
    if nl != -1:
        return nl + 1

    best = -1
    for end in _SENTENCE_ENDS:
        i = text.rfind(end, lo, limit)
        if i != -1:
            best = max(best, i + len(end))
    if best != -1:
        return best

    sp = text.rfind(" ", lo, limit)
    if sp != -1:
        return sp + 1

    cut = limit
    while cut > 1 and (
        unicodedata.category(text[cut])[0] == "M" or text[cut - 1] in _JOINERS
    ):
        cut -= 1
    return cut

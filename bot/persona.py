"""Parsing for `/setup ...` (pure: no Telegram, no network -> unit-testable).

Only one subcommand exists today:

    /setup                    -> usage
    /setup prompt             -> show the active system prompt
    /setup prompt reset       -> back to the built-in default
    /setup prompt <text...>   -> set it (multi-line is fine)

We parse the raw message text instead of `context.args`: PTB splits args on
whitespace, which would flatten the newlines out of a multi-line persona.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Characters, not tokens. Burmese tokenises badly (roughly 1+ token per char), and
# the prompt is re-sent on every request inside an 8k-token window, so keep it small.
MAX_PROMPT_CHARS = 1500


class Kind(Enum):
    HELP = "help"
    SHOW = "show"
    RESET = "reset"
    SET = "set"
    TOO_LONG = "too_long"


@dataclass(frozen=True)
class SetupCommand:
    kind: Kind
    text: str = ""


def parse_setup(raw: str, max_chars: int = MAX_PROMPT_CHARS) -> SetupCommand:
    """Parse the full text of a `/setup` message (command word included)."""
    # [command, subcommand, rest]; maxsplit keeps newlines inside `rest` intact.
    parts = raw.strip().split(None, 2)
    if len(parts) < 2 or parts[1].lower() != "prompt":
        return SetupCommand(Kind.HELP)
    if len(parts) == 2:
        return SetupCommand(Kind.SHOW)
    rest = parts[2].strip()
    if rest.lower() == "reset":  # whole remainder only: "reset your tone" is a prompt
        return SetupCommand(Kind.RESET)
    if len(rest) > max_chars:
        return SetupCommand(Kind.TOO_LONG, rest)
    return SetupCommand(Kind.SET, rest)

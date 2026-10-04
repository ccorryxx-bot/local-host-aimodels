"""Post-mortem helpers for when llama-server drops a connection mid-reply.

Their output goes to the Actions log, which is PUBLIC (see start_llama.sh, which
keeps llama-server output in a file for that reason). So only non-content facts
are emitted: memory numbers, plus error-looking lines from the llama-server log,
truncated. Never prompts or replies.
"""

from __future__ import annotations

import os
import re
from collections import deque
from pathlib import Path

_WANTED = ("MemTotal", "MemAvailable", "SwapFree")
_ERRORISH = re.compile(
    r"error|abort|assert|signal|fatal|out of memory|oom|failed|exception|segmentation",
    re.IGNORECASE,
)


def meminfo_summary(path: str = "/proc/meminfo") -> str:
    """One-line memory snapshot, e.g. 'MemTotal 16008 MB, MemAvailable 3120 MB'."""
    vals: dict[str, int] = {}
    try:
        for line in Path(path).read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in _WANTED:
                vals[key] = int(rest.split()[0]) // 1024  # kB -> MB
    except (OSError, ValueError, IndexError):
        return "meminfo unavailable"
    return ", ".join(f"{k} {vals[k]} MB" for k in _WANTED if k in vals) or "meminfo unavailable"


def llama_log_errors(
    path: str | None = None, limit: int = 12, width: int = 160, scan: int = 3000
) -> list[str]:
    """Last `limit` error-looking lines from the tail of the llama-server log.

    Each line is cut to `width` chars so a stray line that happens to quote
    content cannot leak much. Missing/unreadable log -> empty list.
    """
    log_path = path or str(Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "llama-server.log")
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=scan)  # bounded memory even for a big log
    except OSError:
        return []
    hits = [ln.strip()[:width] for ln in tail if _ERRORISH.search(ln)]
    return hits[-limit:]

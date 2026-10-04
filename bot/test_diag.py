"""Tests for diag.py (run from bot/: python -m unittest test_diag -v)."""

import tempfile
import unittest
from pathlib import Path

from diag import llama_log_errors, meminfo_summary


class MeminfoTests(unittest.TestCase):
    def test_parses_and_converts_kb_to_mb(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "meminfo"
            p.write_text("MemTotal:       16384000 kB\nMemFree: 1 kB\nMemAvailable:    2048000 kB\nSwapFree: 0 kB\n")
            self.assertEqual(
                meminfo_summary(str(p)),
                "MemTotal 16000 MB, MemAvailable 2000 MB, SwapFree 0 MB",
            )

    def test_missing_file(self) -> None:
        self.assertEqual(meminfo_summary("/nonexistent/meminfo"), "meminfo unavailable")


class LogErrorTests(unittest.TestCase):
    def _log(self, text: str) -> str:
        d = tempfile.mkdtemp()
        p = Path(d) / "llama-server.log"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def test_only_error_lines_last_n_and_truncated(self) -> None:
        lines = ["srv  log_server_r: request: POST /v1/chat/completions 200"] * 5
        lines += [f"ggml_abort: fatal error number {i} " + "x" * 400 for i in range(20)]
        got = llama_log_errors(self._log("\n".join(lines)), limit=3, width=50)
        self.assertEqual(len(got), 3)
        self.assertTrue(all(len(g) <= 50 for g in got))
        self.assertIn("number 19", got[-1] + "")  # newest kept (width cut may trim)

    def test_clean_log_gives_nothing(self) -> None:
        self.assertEqual(llama_log_errors(self._log("all good\nslot released\n")), [])

    def test_missing_log(self) -> None:
        self.assertEqual(llama_log_errors("/nonexistent/llama.log"), [])


if __name__ == "__main__":
    unittest.main()

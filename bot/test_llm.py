"""Run from the bot/ directory:  python -m unittest test_llm -v"""

import asyncio
import json
import unittest

import httpx

from llm import LlamaError, stream_chat


def _sse(*deltas: str) -> bytes:
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": d}}]}) for d in deltas
    ]
    return ("\n\n".join(lines) + "\n\ndata: [DONE]\n\n").encode()


async def _collect(handler, **kw) -> tuple[list[str], list[dict]]:
    seen: list[dict] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(wrapped)) as client:
        out = [
            d
            async for d in stream_chat(
                client,
                base_url="http://x",
                api_key="k",
                messages=[{"role": "user", "content": "hi"}],
                **kw,
            )
        ]
    return out, seen


class StreamChatTests(unittest.TestCase):
    def test_streams_deltas_and_disables_thinking(self) -> None:
        out, seen = asyncio.run(
            _collect(lambda r: httpx.Response(200, content=_sse("Hel", "lo")))
        )
        self.assertEqual("".join(out), "Hello")
        self.assertEqual(seen[0]["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(seen[0]["temperature"], 0.7)
        self.assertNotIn("top_k", seen[0])

    def test_sampling_overrides_apply(self) -> None:
        _, seen = asyncio.run(
            _collect(
                lambda r: httpx.Response(200, content=_sse("x")),
                sampling={"temperature": 1.0, "top_p": 0.95, "top_k": 64},
            )
        )
        self.assertEqual(seen[0]["temperature"], 1.0)
        self.assertEqual(seen[0]["top_p"], 0.95)
        self.assertEqual(seen[0]["top_k"], 64)

    def test_http_error_raises(self) -> None:
        with self.assertRaises(LlamaError):
            asyncio.run(_collect(lambda r: httpx.Response(500, text="boom")))

    def test_empty_stream_raises(self) -> None:
        with self.assertRaises(LlamaError):
            asyncio.run(_collect(lambda r: httpx.Response(200, content=_sse())))


if __name__ == "__main__":
    unittest.main()

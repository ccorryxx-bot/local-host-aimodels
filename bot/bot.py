"""Telegram <-> llama-server bridge, long-polling only.

Runs as the last step of the `host` job, after llama-server is up and healthy.
`Application.run_polling()` deletes the webhook itself on start (the worker set
it; we take over), so the worker stops seeing updates for as long as this
process is alive. Restoring the webhook afterwards is the caller's job
(see scripts/restore_webhook.sh, wired as an `if: always()` step in host.yml)
-- if this process is killed hard (OOM, runner death) there is no Python code
left to run a `finally` block, so that restore step must not depend on this
one exiting cleanly.

History lives in a plain dict in process memory (ROADMAP: "never written to
disk" -- a public repo's Actions logs/artifacts are not where prompts belong).
It is gone the moment this process exits, which is the point.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from telegram import BotCommand, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from llm import LlamaError, stream_chat

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is enough
log = logging.getLogger("bot")

# Registered with Telegram on every boot (see _post_init) so the "/" menu
# always matches what's actually implemented below -- one list to keep in
# sync, not two (this one, plus a copy pasted into a BotFather chat).
COMMANDS = [
    BotCommand("start", "Check the bot is up"),
    BotCommand("stop", "Stop the runner and go offline"),
    BotCommand("reset", "Clear conversation history"),
    BotCommand("model", "Show the current model"),
]

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["TG_ALLOWED_CHAT_ID"])
LLAMA_API_KEY = os.environ["LLAMA_API_KEY"]
LLAMA_PORT = os.environ.get("LLAMA_PORT", "8080")
LLAMA_URL = f"http://127.0.0.1:{LLAMA_PORT}"

MAX_TOKENS = int(os.environ.get("LLAMA_MAX_TOKENS", "800"))
# Kept user+assistant pairs, not raw message count.
MAX_HISTORY_TURNS = int(os.environ.get("MAX_HISTORY_TURNS", "12"))
EDIT_INTERVAL = float(os.environ.get("EDIT_INTERVAL_SECONDS", "0.8"))
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT", "You are a helpful, concise assistant reachable over Telegram."
)
MODELS_CONFIG = Path(__file__).resolve().parent.parent / "config" / "models.json"

# {chat_id: [{"role": ..., "content": ...}, ...]}  -- system prompt not stored here,
# it's prepended fresh on every request so /reset can't accidentally drop it.
_history: dict[int, list[dict[str, str]]] = {}


def _trim_history(history: list[dict[str, str]], max_turns: int) -> None:
    """Keep only the most recent `max_turns` user+assistant pairs, in place."""
    keep = max(0, 2 * max_turns)
    if len(history) > keep:
        del history[: len(history) - keep]


def _owner_only(func):
    """Silently drop updates from anyone but the owner chat.

    No "not authorized" reply: a public bot username that talks back to
    strangers confirms it's alive and worth probing. Silence gives an
    attacker nothing to work with.
    """

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat is None or chat.id != ALLOWED_CHAT_ID:
            return
        return await func(update, context)

    return wrapper


@_owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Already running. Ask me anything, or use /stop, /reset, /model."
    )


@_owner_only
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Stopping. Webhook will be restored in a moment.")
    log.info("stop requested from chat")
    context.application.stop_running()


@_owner_only
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _history.pop(update.effective_chat.id, None)
    await update.message.reply_text("History cleared.")


@_owner_only
async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        cfg = json.loads(MODELS_CONFIG.read_text())
        current = cfg["models"][cfg["default"]]
        lines = [f"Current: {cfg['default']} ({current['file']})"]
        others = [m for m in cfg["models"] if m != cfg["default"]]
        if others:
            lines.append("Configured (not live-switchable yet): " + ", ".join(others))
        await update.message.reply_text("\n".join(lines))
    except (OSError, KeyError, ValueError) as e:
        log.warning("model config read failed: %s", e)
        await update.message.reply_text("Could not read model config.")


def _shown(buf: str) -> str:
    """Clamp to comfortably under Telegram's 4096-char message cap.

    Splitting into multiple messages instead would break streaming edits
    (there'd be no single message left to keep editing), so truncate.
    """
    return buf if len(buf) <= 3900 else buf[:3900] + "\n\n[truncated]"


async def _stream_reply(client: httpx.AsyncClient, sent, messages: list[dict]) -> str:
    """Stream a completion into `sent` via periodic edits. Returns the full text.

    Errors are rendered into the message itself (whatever streamed so far,
    plus a short error suffix) rather than raised, since by the time we know
    something went wrong the user already has a "..." placeholder on screen
    that needs to become *something* final.
    """
    buf = ""
    last_edit_time = 0.0
    last_edit_text = ""

    async def safe_edit(new_text: str) -> None:
        nonlocal last_edit_text
        if new_text == last_edit_text or not new_text:
            return
        try:
            await sent.edit_text(new_text)
        except RetryAfter as e:
            # asyncio.sleep, not time.sleep: this coroutine shares an event
            # loop with the bot's own getUpdates polling, and a blocking
            # sleep here would stall that too.
            await asyncio.sleep(e.retry_after)
            await sent.edit_text(new_text)
        except BadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise
        last_edit_text = new_text

    try:
        async for delta in stream_chat(
            client,
            base_url=LLAMA_URL,
            api_key=LLAMA_API_KEY,
            messages=messages,
            max_tokens=MAX_TOKENS,
        ):
            buf += delta
            now = time.monotonic()
            if now - last_edit_time >= EDIT_INTERVAL:
                await safe_edit(_shown(buf))
                last_edit_time = now
    except LlamaError as e:
        log.warning("llama error: %s", e)
        await safe_edit(_shown(buf + f"\n\n[error: {e}]") if buf else f"Error: {e}")
        return buf
    except httpx.HTTPError as e:
        log.warning("http error talking to llama-server: %s", e)
        suffix = "\n\n[connection error]"
        await safe_edit(_shown(buf + suffix) if buf else "Connection error.")
        return buf

    await safe_edit(_shown(buf))  # land the final text even inside the throttle gap
    return buf


@_owner_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = update.message.text
    if not text:
        return

    history = _history.setdefault(chat_id, [])
    history.append({"role": "user", "content": text})
    _trim_history(history, MAX_HISTORY_TURNS)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    sent = await update.message.reply_text("...")
    client: httpx.AsyncClient = context.bot_data["http_client"]

    reply = await _stream_reply(client, sent, messages)

    history.append({"role": "assistant", "content": reply})
    _trim_history(history, MAX_HISTORY_TURNS)


async def _post_init(app: Application) -> None:
    app.bot_data["http_client"] = httpx.AsyncClient(timeout=120)
    await app.bot.set_my_commands(COMMANDS)
    log.info("bot ready, owner chat=%s, model server=%s", ALLOWED_CHAT_ID, LLAMA_URL)


async def _post_shutdown(app: Application) -> None:
    client: httpx.AsyncClient | None = app.bot_data.get("http_client")
    if client is not None:
        await client.aclose()


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # drop_pending_updates=False: anything Telegram queued while the webhook
    # couldn't be reached (there shouldn't be much) is still worth answering.
    app.run_polling(drop_pending_updates=False, allowed_updates=["message"])


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        sys.exit(f"missing required env var: {e}")

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
import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from telegram import BotCommand, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from chunking import split_point
from idle import IdleAction, IdleTracker
from diag import llama_log_errors, meminfo_summary
from llm import LlamaError, health, stream_chat

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is enough
log = logging.getLogger("bot")

# Registered with Telegram on every boot (see _post_init) so the "/" menu
# always matches what's actually implemented below -- one list to keep in
# sync, not two (this one, plus a copy pasted into a BotFather chat).
COMMANDS = [
    BotCommand("start", "Start runner (optional model id)"),
    BotCommand("stop", "Stop the runner and go offline"),
    BotCommand("reset", "Clear conversation history"),
    BotCommand("model", "Show current and available models"),
    BotCommand("status", "Show runner and model status"),
]

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["TG_ALLOWED_CHAT_ID"])
LLAMA_API_KEY = os.environ["LLAMA_API_KEY"]
LLAMA_PORT = os.environ.get("LLAMA_PORT", "8080")
LLAMA_URL = f"http://127.0.0.1:{LLAMA_PORT}"

# Kept user+assistant pairs, not raw message count.
MAX_HISTORY_TURNS = int(os.environ.get("MAX_HISTORY_TURNS", "12"))
EDIT_INTERVAL = float(os.environ.get("EDIT_INTERVAL_SECONDS", "0.8"))
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT", "You are a helpful, concise assistant reachable over Telegram."
)
MODELS_CONFIG = Path(__file__).resolve().parent.parent / "config" / "models.json"


def _load_registry() -> dict:
    try:
        return json.loads(MODELS_CONFIG.read_text())
    except (OSError, ValueError) as e:
        log.warning("model config read failed: %s", e)
        return {}


# Which model is live is decided by the workflow (MODEL_ID env, set from the
# /start argument); the registry only describes it. Falls back to the default
# so running bot.py by hand still works.
_REGISTRY = _load_registry()
MODEL_ID = os.environ.get("MODEL_ID") or _REGISTRY.get("default", "")
_MODEL = _REGISTRY.get("models", {}).get(MODEL_ID, {})
MODEL_LABEL = _MODEL.get("label", MODEL_ID or "unknown")
# Whitelist: a typo in models.json must not be able to overwrite `messages`/`stream`.
_SAMPLING_KEYS = {"temperature", "top_p", "top_k", "min_p", "repeat_penalty"}
SAMPLING = {k: v for k, v in _MODEL.get("sampling", {}).items() if k in _SAMPLING_KEYS}
MAX_TOKENS = int(os.environ.get("LLAMA_MAX_TOKENS") or _MODEL.get("max_tokens", 800))
# Rough history cap in characters: ~2 chars per token of the room left after the
# reply. A heuristic (Burmese tokenises worse than English), but it stops long
# coding replies from silently overflowing the context window.
HISTORY_CHAR_BUDGET = max(2000, (int(_MODEL.get("ctx", 4096)) - MAX_TOKENS) * 2)
CHUNK = 3800  # per Telegram message; comfortably under the 4096-char cap

# Idle shutdown (ROADMAP Phase 6). IDLE_TIMEOUT_MINUTES=0 disables it. The
# warning fires once, IDLE_WARN_MINUTES before the cutoff.
IDLE_TIMEOUT_S = int(float(os.environ.get("IDLE_TIMEOUT_MINUTES", "20")) * 60)
IDLE_WARN_S = int(float(os.environ.get("IDLE_WARN_MINUTES", "5")) * 60)
IDLE_CHECK_S = 30  # watchdog poll interval; worst-case overshoot of the timeout
_idle = IdleTracker(IDLE_TIMEOUT_S, IDLE_WARN_S)

# {chat_id: [{"role": ..., "content": ...}, ...]}  -- system prompt not stored here,
# it's prepended fresh on every request so /reset can't accidentally drop it.
_history: dict[int, list[dict[str, str]]] = {}


def _trim_history(
    history: list[dict[str, str]], max_turns: int, max_chars: int
) -> None:
    """Keep the most recent `max_turns` pairs and at most `max_chars`, in place.

    Always drops whole user+assistant pairs from the front: Gemma-style chat
    templates reject a conversation that starts with an assistant turn.
    """
    # Odd length means the newest message is an unanswered user turn (we trim
    # right after appending it). Keep it on top of the N pairs, otherwise the
    # cut lands mid-pair and the history starts with an assistant turn.
    keep = max(0, 2 * max_turns) + len(history) % 2
    if len(history) > keep:
        del history[: len(history) - keep]
    while len(history) > 2 and sum(len(m["content"]) for m in history) > max_chars:
        del history[:2]


def _owner_only(func):
    """Silently drop updates from anyone but the owner chat.

    No "not authorized" reply: a public bot username that talks back to
    strangers confirms it's alive and worth probing. Silence gives an
    attacker nothing to work with.
    """

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat is None or chat.id != ALLOWED_CHAT_ID:
            return  # strangers must not be able to reset the idle clock
        # Touch on both ends: a slow streamed reply (CPU, 3-6 tok/s) can run
        # for minutes, and that time must not count as idle.
        _idle.touch()
        try:
            return await func(update, context)
        finally:
            _idle.touch()

    return wrapper


@_owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    wanted = context.args[0].lower() if context.args else ""
    if wanted and wanted != MODEL_ID:
        # Switching means a fresh runner: the worker only sees /start while offline.
        text = f"Running {MODEL_ID}, not {wanted}. To switch: /stop, then /start {wanted}."
    else:
        text = f"Already running {MODEL_LABEL}. Ask me anything, or use /stop, /reset, /model."
    await update.message.reply_text(text)


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
    models = _REGISTRY.get("models", {})
    if not models:
        await update.message.reply_text("Could not read model config.")
        return
    others = [m for m in models if m != MODEL_ID]
    lines = [f"Current: {MODEL_LABEL} [{MODEL_ID}]"]
    if others:
        lines.append("Others: " + ", ".join(others))
        lines.append("Switch: /stop, then /start <id>")
    await update.message.reply_text("\n".join(lines))


@_owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Report the active model; the Worker owns status checks while offline."""
    await update.message.reply_text(f"Online. Running {MODEL_LABEL} [{MODEL_ID}].")


async def _reply_retry(message, text: str):
    """reply_text with one retry on Telegram flood control."""
    try:
        return await message.reply_text(text)
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return await message.reply_text(text)


async def _stream_reply(client: httpx.AsyncClient, sent, messages: list[dict], send_new) -> str:
    """Stream a completion into Telegram via periodic edits. Returns the full text.

    Replies longer than CHUNK chars roll over: the current message is finalised
    at a sensible break (see chunking.split_point) and streaming continues in a
    fresh message, so long code answers arrive whole instead of truncated.

    Errors are rendered into the message itself (whatever streamed so far,
    plus a short error suffix) rather than raised, since by the time we know
    something went wrong the user already has a "..." placeholder on screen
    that needs to become *something* final. The suffix is never part of the
    returned text, so it can't leak into the conversation history.
    """
    buf = ""
    start = 0  # buf[start:] is what `sent` currently shows
    last_edit_time = 0.0
    last_edit_text = ""

    async def safe_edit(new_text: str) -> None:
        nonlocal last_edit_text
        # Telegram rejects empty/whitespace-only text; a chunk can begin with "\n".
        if new_text == last_edit_text or not new_text.strip():
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

    async def render(text: str, *, force: bool = False) -> None:
        nonlocal sent, start, last_edit_text, last_edit_time
        while len(text) - start > CHUNK:
            cut = start + split_point(text[start:], CHUNK)
            await safe_edit(text[start:cut])  # finalise this message
            start = cut
            sent = await send_new("...")
            last_edit_text = "..."
        now = time.monotonic()
        if force or now - last_edit_time >= EDIT_INTERVAL:
            await safe_edit(text[start:])
            last_edit_time = now

    try:
        async for delta in stream_chat(
            client,
            base_url=LLAMA_URL,
            api_key=LLAMA_API_KEY,
            messages=messages,
            max_tokens=MAX_TOKENS,
            sampling=SAMPLING,
        ):
            buf += delta
            await render(buf)
    except LlamaError as e:
        log.warning("llama error: %s", e)
        await render(buf + f"\n\n[error: {e}]" if buf else f"Error: {e}", force=True)
        return buf
    except httpx.HTTPError as e:
        # str(e) is empty for most httpx errors (ReadTimeout, ...), so name the
        # type, and check whether llama-server is still up: "timeout, alive"
        # (slow/stalled) and "closed, down" (crashed) need different fixes.
        kind = type(e).__name__
        try:
            alive = await health(client, base_url=LLAMA_URL)
        except Exception:  # diagnostics must never mask the original error
            alive = False
        state = "alive" if alive else "down"
        log.warning(
            "http error talking to llama-server: %s (%s) | server %s | %s",
            kind, str(e) or "no message", state, meminfo_summary(),
        )
        for line in llama_log_errors():
            log.warning("llama-server log: %s", line)
        tag = f"{kind}, server {state}"
        await render(
            buf + f"\n\n[connection error: {tag}]" if buf else f"Connection error: {tag}.",
            force=True,
        )
        return buf

    await render(buf, force=True)  # land the final text even inside the throttle gap
    return buf


@_owner_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = update.message.text
    if not text:
        return

    history = _history.setdefault(chat_id, [])
    history.append({"role": "user", "content": text})
    _trim_history(history, MAX_HISTORY_TURNS, HISTORY_CHAR_BUDGET)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    sent = await update.message.reply_text("...")
    client: httpx.AsyncClient = context.bot_data["http_client"]

    reply = await _stream_reply(
        client, sent, messages, lambda t: _reply_retry(update.message, t)
    )

    if reply:
        history.append({"role": "assistant", "content": reply})
        _trim_history(history, MAX_HISTORY_TURNS, HISTORY_CHAR_BUDGET)
    else:
        # Failed before any text: drop the unanswered user turn so roles keep
        # alternating (strict chat templates reject two user turns in a row).
        history.pop()


async def _notify_owner(app: Application, text: str) -> None:
    """Best-effort message to the owner. Never raises: a failed courtesy
    message must not stop the watchdog from doing its actual job."""
    try:
        await app.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=text)
    except TelegramError as e:
        log.warning("owner notify failed: %s", e)


async def _idle_watchdog(app: Application) -> None:
    """Stop the bot (and therefore end the job) after a quiet stretch.

    stop_running() makes run_polling() return, so bot.py exits 0 and the
    workflow's `if: always()` step restores the webhook -- same path as /stop.
    """
    warn_min = max(1, IDLE_WARN_S // 60)
    timeout_min = max(1, IDLE_TIMEOUT_S // 60)
    while True:
        await asyncio.sleep(IDLE_CHECK_S)
        try:
            action = _idle.check()
            if action is IdleAction.WARN:
                await _notify_owner(
                    app,
                    f"Idle for a while. Shutting down in ~{warn_min} min "
                    "unless you send a message.",
                )
            elif action is IdleAction.STOP:
                log.info("idle timeout reached, stopping")
                await _notify_owner(
                    app,
                    f"Idle for {timeout_min} min - shutting down. "
                    "Send /start to boot me again.",
                )
                app.stop_running()
                return
        except Exception:  # noqa: BLE001 - a dead watchdog means no idle shutdown
            log.exception("idle watchdog iteration failed; continuing")


async def _post_init(app: Application) -> None:
    app.bot_data["http_client"] = httpx.AsyncClient(timeout=120)
    await app.bot.set_my_commands(COMMANDS)
    _idle.touch()  # the countdown starts when the bot is actually ready
    if IDLE_TIMEOUT_S > 0:
        # Keep a reference: the event loop only holds tasks weakly.
        app.bot_data["idle_task"] = asyncio.create_task(_idle_watchdog(app))
    log.info("bot ready, model=%s, owner chat=%s, server=%s", MODEL_ID, ALLOWED_CHAT_ID, LLAMA_URL)


async def _post_shutdown(app: Application) -> None:
    task: asyncio.Task | None = app.bot_data.get("idle_task")
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
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
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # drop_pending_updates=False: anything Telegram queued while the webhook
    # couldn't be reached (there shouldn't be much) is still worth answering.
    app.run_polling(drop_pending_updates=False, allowed_updates=["message"])


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        sys.exit(f"missing required env var: {e}")

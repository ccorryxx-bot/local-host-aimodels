# Roadmap: local-host-aimodels

On-demand local AI (chat + image generation) hosted on a GitHub Actions runner, controlled entirely from Telegram.

**Goal:** `/start` in Telegram spins up the runner, `/stop` (or idle timeout) shuts it down. No always-on server, no third-party AI API.

## Architecture

```
Telegram <-> Cloudflare Worker --workflow_dispatch--> GitHub Actions runner
 (owner)     (always-on, /start only)                  |- llama-server (chat)
    ^                                                  |- stable-diffusion.cpp (image)
    '------------- long polling (runner ON) ---------- '- bot.py
```

- **Worker** is the only always-on part. It receives Telegram webhooks while the runner is off and dispatches the workflow.
- **Runner** deletes the webhook on boot and long-polls Telegram directly. On exit (`if: always()`) it restores the webhook.
- **Worker cron** (every 5 min) self-heals: if no run is active and the webhook is missing, it sets it again.

## Repo structure (target)

```
local-host-aimodels/
|- .github/workflows/host.yml
|- worker/              index.js, wrangler.toml
|- bot/                 bot.py, llm.py, imagegen.py, requirements.txt
|- config/models.json   model registry
|- scripts/             setup_runner.sh, fetch_models.sh
|- ROADMAP.md
'- README.md
```

## Secrets

| Where | Name | Notes |
|---|---|---|
| Actions | `TG_BOT_TOKEN` | Bot token |
| Actions | `TG_ALLOWED_CHAT_ID` | Owner chat only |
| Worker | `TG_BOT_TOKEN` | Same bot |
| Worker | `GH_DISPATCH_PAT` | Fine-grained, this repo only, Actions: write |
| Worker | `TG_WEBHOOK_SECRET` | Verified via `X-Telegram-Bot-Api-Secret-Token` |

## Phases

### Phase 1: Repo + secrets
- [x] Public repo created
- [x] Actions secrets: `TG_BOT_TOKEN`, `TG_ALLOWED_CHAT_ID`, `TG_WEBHOOK_SECRET`
- [ ] Remaining secrets: `GH_DISPATCH_PAT`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` (Worker secrets are pushed from Actions secrets on deploy)
- **Exit:** all 6 secrets in place

### Phase 2: Cloudflare Worker (trigger)
Deploy path: push to `worker/**` -> `deploy-worker.yml` -> `wrangler deploy` (source of truth stays in this repo).
- [x] Webhook handler with secret-token check
- [x] Reject any chat ID except the owner
- [x] `/start` dispatches the workflow, `/status` reports run state
- [x] Cron self-heal for webhook
- **Exit:** `/start` in Telegram launches a workflow run

### Phase 3: Workflow and runner prep
- [x] `concurrency` group (one run at a time), `timeout-minutes: 330`
- [x] Free disk first (runner has ~14 GB): remove dotnet, android, ghc
- [x] llama.cpp prebuilt release binary (no compile) — pinned `b11228`, ubuntu-x64 CPU build
- [x] Model download + `actions/cache` (repo cache limit is 10 GB) — Qwen2.5-7B Q4_K_M, ~4.7 GB, restore/save split so a partial run doesn't lose the download
- **Exit:** `llama-server` up, answers a curl request — verified by `smoke_test.sh` (`/v1/chat/completions`, checks token count + measures tok/s)

### Phase 4: Bot (chat)
- [x] Python, long polling, `deleteWebhook` on boot — `Application.run_polling()` does this itself
- [x] Owner-only allowlist — silent drop for any other chat ID, no reply (nothing for a prober to see)
- [x] History kept in memory only, never written to disk — plain dict, gone when the process exits
- [x] Commands: `/stop`, `/model`, `/reset` (`/stop` calls `Application.stop_running()`, exits the job cleanly)
- [x] Streamed replies via message edit — one edit / 1.2s by default (`EDIT_INTERVAL_SECONDS`), final edit always lands
- [x] Telegram flood control (429): `bot/pacing.py` honours `retry_after` without blocking the model stream, skips edits during the cooldown, then widens the edit interval (x2, cap 6s) for the rest of the session. Edits that must land (rollover, end of reply) wait the cooldown out. Intermediate edit errors never abort a reply.
- [x] `if: always()` step restores webhook — direct `setWebhook`, not the worker's `/heal` (see restore_webhook.sh)
- [x] Model select: `/start <id>` (worker -> `workflow_dispatch` input `model`); registry in `config/models.json`; `/model` lists ids; switching = `/stop`, then `/start <id>`
- [x] Long replies roll over into extra messages (no more `[truncated]`); history capped by chars as well as turns
- **Exit:** ask a question in Telegram, get an answer

### Phase 5: Image generation
- [ ] `stable-diffusion.cpp` with SD-Turbo (1-4 steps, 512x512), SD 1.5 fallback
- [ ] `/imagine <prompt>` runs as subprocess, frees RAM after
- [ ] Single-job queue
- [ ] Send via `sendPhoto`
- **Exit:** image in about 1-2 minutes

### Phase 6: Lifecycle and hardening
- [x] Idle shutdown after 20 min without messages — `bot/idle.py` (pure, unit-tested) + watchdog task in `bot.py`; owner-only activity resets the clock, one warning 5 min before; `IDLE_TIMEOUT_MINUTES` / `IDLE_WARN_MINUTES` env, `0` disables
- [ ] Graceful `/stop`: stop bot, restore webhook, end job
- [ ] RAM/disk watchdog, Telegram alert on failure
- **Exit:** `/stop`, idle timeout and the 6 h limit all leave the webhook restored

### Phase 7: Benchmark and docs
- [ ] Tokens/sec, image time, peak RAM per model
- [ ] Results table in README
- **Exit:** model choice backed by data

## Public repo rules (non-negotiable)

1. Workflow logs are public: the bot never logs prompts or replies.
2. Artifacts are public: images go to Telegram only, never uploaded as artifacts.
3. Never `echo` secrets.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Inference | `llama-server` | Lighter than Ollama in CI, prebuilt binaries |
| Chat model | Qwen2.5-7B-Instruct Q4_K_M (default) | Fast fallback, weak Burmese |
| Coding + multilingual | Gemma 4 12B QAT (`gemma4-12b`) | LiveCodeBench v6 72%, Apache 2.0, 6.7 GB. **Burmese quality unverified: test first**, then consider making it the default |
| Burmese-first | AI4Burmese Padauk Q8 (`padauk`) | Gemma 4 fine-tune, community model, 8 GB, unproven |
| General fallback | Dolphin 3.0 Llama 3.1 8B Abliterated Q4_K_M (`dolphin3-8b-abliterated`) | ~5.0 GB; lower resource use and broad chat/coding fallback |
| Quality fallback | Qwen3 14B Abliterated Q4_K_M (`qwen3-14b-abliterated`) | ~9.1 GB; stronger quality fallback, slower and memory-intensive on CPU |
| Bot language | Python | Fast to build, mature Telegram libraries |
| Webhook handling | Swap + Worker self-heal | Simplest for v1 |

## Later (experimental)

- Worker as a message relay (KV queue) so the runner never touches the webhook. More robust, more code.

## Known limits

- Repo cache limit is 10 GB, so the five models (about 33 GB) cannot all stay cached; least-recently-used ones get evicted and re-download (+3-6 min). The abliteration fallbacks are downloaded on demand via `/start <model-id>`.
- `gemma4-12b` / `padauk` speeds are estimates until benchmarked (Phase 7). `padauk` and Gemma 4 GGUF loading via `-m` (text only, no mmproj) is untested on the runner.

- CPU-only runner: roughly 3-6 tokens/sec on a 7B Q4 model.
- 16 GB RAM, ~14 GB disk: models must fit together (chat about 5 GB, image about 2-4 GB).
- Do not use for 24/7 hosting. On-demand sessions only.

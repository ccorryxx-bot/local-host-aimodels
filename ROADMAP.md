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
- [ ] `concurrency` group (one run at a time), `timeout-minutes: 330`
- [ ] Free disk first (runner has ~14 GB): remove dotnet, android, ghc
- [ ] llama.cpp prebuilt release binary (no compile)
- [ ] Model download + `actions/cache` (repo cache limit is 10 GB)
- **Exit:** `llama-server` up, answers a curl request

### Phase 4: Bot (chat)
- [ ] Python, long polling, `deleteWebhook` on boot
- [ ] Owner-only allowlist
- [ ] History kept in memory only, never written to disk
- [ ] Commands: `/stop`, `/model`, `/reset`
- [ ] Streamed replies via message edit
- [ ] `if: always()` step restores webhook
- **Exit:** ask a question in Telegram, get an answer

### Phase 5: Image generation
- [ ] `stable-diffusion.cpp` with SD-Turbo (1-4 steps, 512x512), SD 1.5 fallback
- [ ] `/imagine <prompt>` runs as subprocess, frees RAM after
- [ ] Single-job queue
- [ ] Send via `sendPhoto`
- **Exit:** image in about 1-2 minutes

### Phase 6: Lifecycle and hardening
- [ ] Idle shutdown after 20 min without messages
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
| Chat model | Qwen2.5-7B-Instruct Q4_K_M | Switchable to Coder 7B via `/model` |
| Bot language | Python | Fast to build, mature Telegram libraries |
| Webhook handling | Swap + Worker self-heal | Simplest for v1 |

## Later (experimental)

- Worker as a message relay (KV queue) so the runner never touches the webhook. More robust, more code.

## Known limits

- CPU-only runner: roughly 3-6 tokens/sec on a 7B Q4 model.
- 16 GB RAM, ~14 GB disk: models must fit together (chat about 5 GB, image about 2-4 GB).
- Do not use for 24/7 hosting. On-demand sessions only.

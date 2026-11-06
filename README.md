# local-host-aimodels

On-demand local AI chat + image generation on a GitHub Actions runner, controlled from Telegram.

Status: in progress (Phases 1-4 done). See [ROADMAP.md](ROADMAP.md).

## Chat models

The workflow downloads the selected GGUF model on demand from Hugging Face. Start a session from Telegram with `/start`, which shows a model picker (buttons built from `config/models.json`); `/start <model-id>` skips the picker. `/model` lists the available IDs.

| Model ID | Use case | Approx. download |
|---|---|---:|
| `qwen2.5-7b` | Fast default | 4.7 GB |
| `gemma4-12b` | Coding and multilingual | 6.7 GB |
| `padauk` | Burmese-first experiment | 8 GB |
| `dolphin3-8b-abliterated` | Resource-friendly abliteration fallback | 4.9 GB |
| `qwen3-14b-abliterated` | Higher-quality abliteration fallback | 9.0 GB |
| `gemma4-12b-abl` | Gemma 4 12B QAT, abliterated (huihui-ai) | 7.4 GB |
| `gemma4-e4b-abl` | Gemma 4 E4B QAT, abliterated, faster | 5.3 GB |
| `gemma4-12b-coder-abl` | Coding fine-tune of Gemma 4 12B, abliterated | 7.5 GB |
| `qwen25-coder-14b-abl` | Qwen2.5-Coder 14B, abliterated | 9.0 GB |
| `qwen3-coder-30b-abl` | Qwen3-Coder 30B-A3B MoE, abliterated, IQ3_XS (experimental: ~12.7 GB file on a 16 GB runner, too big for the 10 GB cache, so it downloads every run) | 12.7 GB |

The abliterated entries are registered in `config/models.json` and use the Q4_K_M GGUF files from their Hugging Face repositories. Downloads are validated for the GGUF magic header and a minimum file size before llama-server starts.

Because GitHub Actions cache storage is limited, models are cached per model and may be re-downloaded when cache entries are evicted.

## Persona (`/setup prompt`)

Give the model a character from Telegram while a session is running:

| Command | Effect |
|---|---|
| `/setup prompt <text>` | Set the system prompt (multi-line is fine, max 1500 chars). Chat history is cleared so the new persona applies cleanly. |
| `/setup prompt` | Show the current prompt. |
| `/setup prompt reset` | Back to the built-in default (also deletes the saved copy). |

The prompt applies immediately. To make it survive the next `/start`, the bot also saves it (libsodium-encrypted) into the repo secret `BOT_SYSTEM_PROMPT`, which `host.yml` feeds back in as `SYSTEM_PROMPT`. The repo is public, so the prompt is never committed or logged (the bot logs its length only).

One-time setup for saving: create a **fine-grained PAT limited to this repository** with **Secrets: Read and write** (plus the mandatory Metadata: Read-only) and store it as the Actions secret `GH_SECRETS_PAT`. Without it `/setup` still works, for the current session only. `/setup` needs a running session: while the runner is offline, `/start` first.


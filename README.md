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

The two abliteration fallbacks are registered in `config/models.json` and use the Q4_K_M GGUF files from their Hugging Face repositories. Downloads are validated for the GGUF magic header and a minimum file size before llama-server starts.

Because GitHub Actions cache storage is limited, models are cached per model and may be re-downloaded when cache entries are evicted.

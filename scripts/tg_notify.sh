#!/usr/bin/env bash
# Send a Telegram message to the owner chat.
# Usage: tg_notify.sh "text"     Needs: TG_BOT_TOKEN, TG_ALLOWED_CHAT_ID
set -euo pipefail

: "${TG_BOT_TOKEN:?TG_BOT_TOKEN not set}"
: "${TG_ALLOWED_CHAT_ID:?TG_ALLOWED_CHAT_ID not set}"
text="${1:?text required}"

curl -fsS -o /dev/null --max-time 20 "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_ALLOWED_CHAT_ID}" \
  --data-urlencode "text=${text}"

#!/usr/bin/env bash
# Restore the Telegram webhook to the worker. Called as an `if: always()` step
# so it runs on success, on failure, and on cancellation alike.
#
# This calls Telegram's setWebhook directly instead of the worker's own
# /heal endpoint on purpose: heal() first checks whether an Actions run is
# active, and skips if one is -- but while THIS step is executing, GitHub
# still reports this very run as "in_progress", so heal() would see it and
# do nothing. Direct setWebhook has no such self-reference problem. The
# worker's 5-minute cron heal sweep remains the fallback for runs that die
# before this step gets a chance to run at all.
#
# Needs: TG_BOT_TOKEN, TG_WEBHOOK_SECRET, WORKER_URL.
set -euo pipefail

: "${TG_BOT_TOKEN:?TG_BOT_TOKEN not set}"
: "${TG_WEBHOOK_SECRET:?TG_WEBHOOK_SECRET not set}"
: "${WORKER_URL:?WORKER_URL not set}"

res=$(curl -fsS "https://api.telegram.org/bot${TG_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=${WORKER_URL}/tg" \
  --data-urlencode "secret_token=${TG_WEBHOOK_SECRET}" \
  --data-urlencode 'allowed_updates=["message","callback_query"]' \
  --data-urlencode "max_connections=1")

ok=$(jq -r '.ok' <<<"$res")
if [ "$ok" != "true" ]; then
  echo "::error::setWebhook failed: $res"
  exit 1
fi
echo "Webhook restored -> ${WORKER_URL}/tg"

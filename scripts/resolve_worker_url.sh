#!/usr/bin/env bash
# Print this account's workers.dev URL for local-host-aimodels.
# Needs: CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID.
set -euo pipefail

: "${CLOUDFLARE_API_TOKEN:?CLOUDFLARE_API_TOKEN not set}"
: "${CLOUDFLARE_ACCOUNT_ID:?CLOUDFLARE_ACCOUNT_ID not set}"

sub=$(curl -fsS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain" \
  | jq -r '.result.subdomain')
[ -n "$sub" ] && [ "$sub" != "null" ]
echo "https://local-host-aimodels.${sub}.workers.dev"

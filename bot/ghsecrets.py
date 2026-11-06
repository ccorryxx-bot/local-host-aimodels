"""Write/delete one GitHub Actions repository secret over the REST API.

Why this exists: the system prompt is private, the repo is public, and the
runner is ephemeral. A secret is the one place that survives between runs
without being readable by anyone but the workflow.

Security notes:
- GitHub only accepts secrets encrypted with the repo's public key (libsodium
  sealed box), so the plaintext never goes over the wire unencrypted-by-design.
- The secret NAME is a caller-side constant (see bot.py), never taken from chat:
  a Telegram message must not be able to touch TG_BOT_TOKEN and friends.
- Errors carry the HTTP status and GitHub's `message` only. Never the token,
  never the value (the ROADMAP's public-logs rule).
"""

from __future__ import annotations

import base64
import re

import httpx
from nacl import encoding, public

API = "https://api.github.com"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TIMEOUT = 20.0


class SecretsError(Exception):
    """Could not read/write the secret. str(e) is safe to show and log."""


def encrypt_secret(public_key_b64: str, value: str) -> str:
    """Seal `value` for GitHub: libsodium sealed box, base64 in and out."""
    key = public.PublicKey(public_key_b64.encode("ascii"), encoding.Base64Encoder())
    sealed = public.SealedBox(key).encrypt(value.encode("utf-8"))
    return base64.b64encode(sealed).decode("ascii")


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _check(repo: str, name: str) -> None:
    if not _REPO_RE.match(repo):
        raise SecretsError("invalid repository name")
    if not _NAME_RE.match(name):
        raise SecretsError("invalid secret name")


def _why(resp: httpx.Response) -> str:
    try:
        msg = resp.json().get("message", "")
    except ValueError:
        msg = ""
    hint = " (token needs 'Secrets: read and write')" if resp.status_code in (401, 403, 404) else ""
    return f"GitHub API {resp.status_code}: {msg or 'no message'}{hint}"


async def put_secret(
    client: httpx.AsyncClient, *, token: str, repo: str, name: str, value: str
) -> None:
    """Create or overwrite the secret."""
    _check(repo, name)
    base = f"{API}/repos/{repo}/actions/secrets"
    try:
        r = await client.get(f"{base}/public-key", headers=_headers(token), timeout=_TIMEOUT)
        if r.status_code != 200:
            raise SecretsError(_why(r))
        key = r.json()
        sealed = encrypt_secret(key["key"], value)
        r = await client.put(
            f"{base}/{name}",
            headers=_headers(token),
            json={"encrypted_value": sealed, "key_id": key["key_id"]},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise SecretsError(f"network error: {type(e).__name__}") from None
    except (KeyError, ValueError) as e:  # malformed key payload / bad base64
        raise SecretsError(f"unexpected response ({type(e).__name__})") from None
    if r.status_code not in (201, 204):  # 201 created, 204 updated
        raise SecretsError(_why(r))


async def delete_secret(
    client: httpx.AsyncClient, *, token: str, repo: str, name: str
) -> None:
    """Delete the secret. Already gone (404) counts as success."""
    _check(repo, name)
    try:
        r = await client.delete(
            f"{API}/repos/{repo}/actions/secrets/{name}",
            headers=_headers(token),
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise SecretsError(f"network error: {type(e).__name__}") from None
    if r.status_code not in (204, 404):
        raise SecretsError(_why(r))

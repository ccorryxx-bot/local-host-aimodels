"""Run from the bot/ directory:  python -m unittest test_ghsecrets -v

No network: httpx.MockTransport plays GitHub. The "GitHub side" holds a real
libsodium keypair, so we prove the uploaded value really decrypts to the prompt.
"""

import asyncio
import base64
import json
import unittest

import httpx
from nacl import encoding, public

from ghsecrets import SecretsError, delete_secret, encrypt_secret, put_secret

TOKEN = "github_pat_SUPERSECRETTOKEN"
REPO = "owner/repo"


class FakeGitHub:
    def __init__(self, *, key_status=200, put_status=201, delete_status=204) -> None:
        self.priv = public.PrivateKey.generate()
        self.key_status, self.put_status, self.delete_status = key_status, put_status, delete_status
        self.requests: list[httpx.Request] = []

    def pub_b64(self) -> str:
        return self.priv.public_key.encode(encoding.Base64Encoder()).decode()

    def decrypt(self, encrypted_b64: str) -> str:
        return public.SealedBox(self.priv).decrypt(base64.b64decode(encrypted_b64)).decode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path.endswith("/actions/secrets/public-key"):
            if self.key_status != 200:
                return httpx.Response(self.key_status, json={"message": "Resource not accessible"})
            return httpx.Response(200, json={"key_id": "kid1", "key": self.pub_b64()})
        if request.method == "PUT":
            return httpx.Response(self.put_status, json={"message": "nope"} if self.put_status >= 400 else None)
        if request.method == "DELETE":
            return httpx.Response(self.delete_status)
        return httpx.Response(500)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def run(coro):
    return asyncio.run(coro)


class EncryptTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        gh = FakeGitHub()
        sealed = encrypt_secret(gh.pub_b64(), "မင်းနာမည် ကျော်လေး 🔥")
        self.assertEqual(gh.decrypt(sealed), "မင်းနာမည် ကျော်လေး 🔥")

    def test_sealed_box_is_randomised(self) -> None:
        gh = FakeGitHub()
        self.assertNotEqual(encrypt_secret(gh.pub_b64(), "x"), encrypt_secret(gh.pub_b64(), "x"))


class PutSecretTests(unittest.TestCase):
    def test_uploads_encrypted_value_with_key_id(self) -> None:
        gh = FakeGitHub()

        async def go():
            async with gh.client() as c:
                await put_secret(c, token=TOKEN, repo=REPO, name="BOT_SYSTEM_PROMPT", value="You are Kyaw Lay.")

        run(go())
        get, put = gh.requests
        self.assertEqual(get.url.path, "/repos/owner/repo/actions/secrets/public-key")
        self.assertEqual(put.url.path, "/repos/owner/repo/actions/secrets/BOT_SYSTEM_PROMPT")
        self.assertEqual(put.headers["authorization"], f"Bearer {TOKEN}")
        body = json.loads(put.content)
        self.assertEqual(body["key_id"], "kid1")
        self.assertNotIn("Kyaw", body["encrypted_value"])  # not plaintext on the wire
        self.assertEqual(gh.decrypt(body["encrypted_value"]), "You are Kyaw Lay.")

    def test_204_means_updated_and_is_success(self) -> None:
        gh = FakeGitHub(put_status=204)

        async def go():
            async with gh.client() as c:
                await put_secret(c, token=TOKEN, repo=REPO, name="BOT_SYSTEM_PROMPT", value="v")

        run(go())

    def test_missing_permission_gives_actionable_error_without_leaking(self) -> None:
        gh = FakeGitHub(key_status=403)

        async def go():
            async with gh.client() as c:
                await put_secret(c, token=TOKEN, repo=REPO, name="BOT_SYSTEM_PROMPT", value="top secret prompt")

        with self.assertRaises(SecretsError) as cm:
            run(go())
        msg = str(cm.exception)
        self.assertIn("403", msg)
        self.assertIn("Secrets", msg)
        self.assertNotIn(TOKEN, msg)
        self.assertNotIn("top secret prompt", msg)

    def test_put_failure_is_reported(self) -> None:
        gh = FakeGitHub(put_status=422)

        async def go():
            async with gh.client() as c:
                await put_secret(c, token=TOKEN, repo=REPO, name="BOT_SYSTEM_PROMPT", value="v")

        with self.assertRaises(SecretsError) as cm:
            run(go())
        self.assertIn("422", str(cm.exception))

    def test_network_error_names_type_only(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout(f"timeout talking with {TOKEN}")

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as c:
                await put_secret(c, token=TOKEN, repo=REPO, name="BOT_SYSTEM_PROMPT", value="v")

        with self.assertRaises(SecretsError) as cm:
            run(go())
        self.assertEqual(str(cm.exception), "network error: ConnectTimeout")

    def test_rejects_suspicious_repo_or_secret_names(self) -> None:
        gh = FakeGitHub()

        async def go(repo, name):
            async with gh.client() as c:
                await put_secret(c, token=TOKEN, repo=repo, name=name, value="v")

        for repo, name in [("../x/y", "OK_NAME"), ("owner/repo/extra", "OK_NAME"), ("owner/repo", "lower"), ("owner/repo", "A/B")]:
            with self.subTest(repo=repo, name=name), self.assertRaises(SecretsError):
                run(go(repo, name))
        self.assertEqual(gh.requests, [])  # rejected before any request


class DeleteSecretTests(unittest.TestCase):
    def _delete(self, status: int) -> None:
        gh = FakeGitHub(delete_status=status)

        async def go():
            async with gh.client() as c:
                await delete_secret(c, token=TOKEN, repo=REPO, name="BOT_SYSTEM_PROMPT")

        run(go())

    def test_204_ok(self) -> None:
        self._delete(204)

    def test_already_gone_is_ok(self) -> None:
        self._delete(404)

    def test_forbidden_raises(self) -> None:
        with self.assertRaises(SecretsError) as cm:
            self._delete(403)
        self.assertIn("403", str(cm.exception))


if __name__ == "__main__":
    unittest.main()

import time
from urllib.parse import parse_qs

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from ucpc.auth import ISSUER, PLAN_SCOPE, Auth, validate_callback, validate_identity


def test_new_registration_requires_issued_client():
    assert validate_callback(parse_qs("state=s&code=c&client_id=oaiapp_1"), "s", None) == (
        "c",
        "oaiapp_1",
    )


@pytest.mark.parametrize(
    "query",
    [
        "state=wrong&code=c&client_id=oaiapp_1",
        "state=s&code=c",
        "state=s&error=access_denied",
        "state=s&code=c&client_id=dynamic_agent_client",
        "state=s&state=s&code=c&client_id=oaiapp_1",
        "state=невірний&code=c&client_id=oaiapp_1",
        "state=s&code=c&code=d&client_id=oaiapp_1",
    ],
)
def test_bad_callback_rejected(query):
    with pytest.raises(ValueError):
        validate_callback(parse_qs(query), "s", None)


def test_returning_registration_cannot_change_client():
    assert validate_callback(parse_qs("state=s&code=c"), "s", "oaiapp_1") == ("c", "oaiapp_1")
    with pytest.raises(ValueError):
        validate_callback(parse_qs("state=s&code=c&client_id=oaiapp_2"), "s", "oaiapp_1")


def test_oidc_signature_audience_and_nonce(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class Keys:
        def __init__(self, *args, **kwargs):
            pass

        def get_signing_key_from_jwt(self, token):
            from types import SimpleNamespace

            return SimpleNamespace(key=key.public_key())

    monkeypatch.setattr("ucpc.auth.jwt.PyJWKClient", Keys)
    claims = {
        "sub": "user",
        "aud": "oaiapp_1",
        "iss": ISSUER,
        "exp": time.time() + 60,
        "nonce": "n",
    }
    token = jwt.encode(claims, key, algorithm="RS256")
    assert validate_identity(token, "oaiapp_1", "n")["sub"] == "user"
    with pytest.raises(ValueError):
        validate_identity(token, "oaiapp_1", "wrong")
    with pytest.raises(jwt.InvalidAudienceError):
        validate_identity(token, "oaiapp_2", "n")


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows DPAPI")
def test_refresh_rotates_credentials_atomically_without_plaintext(tmp_path, monkeypatch):
    auth = Auth(tmp_path)
    auth._save(
        {
            "client_id": "oaiapp_1",
            "subject": "user",
            "access_token": "old-secret",
            "refresh_token": "old-refresh",
            "scopes": [PLAN_SCOPE],
            "expires_at": 0,
        }
    )
    calls = []

    def refresh(data):
        calls.append(data)
        return {"access_token": "new-secret", "refresh_token": "new-refresh", "expires_in": 3600}

    monkeypatch.setattr("ucpc.auth.token_request", refresh)
    assert auth.access_token() == "new-secret"
    assert auth.access_token() == "new-secret"
    assert len(calls) == 1
    assert calls[0]["refresh_token"] == "old-refresh"
    assert auth._load()["refresh_token"] == "new-refresh"
    assert b"new-secret" not in auth.path.read_bytes()
    assert b"old-secret" not in auth.path.read_bytes()


def test_rejected_token_refresh_reuses_replacement_from_another_worker(tmp_path, monkeypatch):
    auth = Auth(tmp_path)
    auth._save({"client_id": "test", "subject": "test", "access_token": "old",
                "refresh_token": "refresh", "scopes": [PLAN_SCOPE],
                "expires_at": time.time() + 3600})
    calls = []

    def refresh(data):
        calls.append(data)
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600}

    monkeypatch.setattr("ucpc.auth.token_request", refresh)
    assert auth.access_token(rejected_token="old") == "new"
    assert auth.access_token(rejected_token="old") == "new"
    assert len(calls) == 1


def test_refresh_before_a_long_job_uses_requested_validity_margin(tmp_path, monkeypatch):
    auth = Auth(tmp_path)
    auth._save({"client_id": "test", "subject": "test", "access_token": "old",
                "refresh_token": "refresh", "scopes": [PLAN_SCOPE],
                "expires_at": time.time() + 120})
    monkeypatch.setattr("ucpc.auth.token_request", lambda _: {
        "access_token": "new", "refresh_token": "rotated", "expires_in": 3600})
    assert auth.access_token() == "old"
    assert auth.access_token(min_validity=390) == "new"


@pytest.mark.parametrize("offline", [False, True])
def test_logout_clears_tokens_even_when_revoke_is_offline(tmp_path, monkeypatch, offline):
    import httpx

    auth = Auth(tmp_path)
    auth._save(
        {
            "client_id": "oaiapp_test",
            "access_token": "test-access",
            "refresh_token": "test-refresh",
            "id_token": "test-id",
        }
    )

    def revoke(*args, **kwargs):
        if offline:
            raise httpx.ConnectError("synthetic connection failure")
        return httpx.Response(200)

    monkeypatch.setattr("ucpc.auth.httpx.post", revoke)
    assert auth.logout() is (not offline)
    assert auth._load() == {"client_id": "oaiapp_test"}
    assert "вхід потрібен" in auth.info()


def test_damaged_credential_file_has_safe_error(tmp_path):
    auth = Auth(tmp_path)
    auth.path.write_bytes(b"synthetic corrupt encrypted credentials")
    with pytest.raises(RuntimeError, match="прочитати") as failure:
        auth.access_token()
    assert "credentials" not in str(failure.value)


def test_model_catalog_excludes_hidden_models(tmp_path, monkeypatch):
    import httpx

    auth = Auth(tmp_path)
    monkeypatch.setattr(auth, "access_token", lambda: "synthetic")
    monkeypatch.setattr(
        "ucpc.auth.httpx.get",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={
                "models": [
                    {"slug": "visible", "visibility": "list"},
                    {"slug": "hidden", "visibility": "hidden"},
                ]
            },
        ),
    )
    assert auth.models() == [{"slug": "visible", "visibility": "list"}]


def test_native_credentials_lock_keeps_stable_file_identity_after_release(tmp_path):
    auth = Auth(tmp_path)
    auth.info()
    path = tmp_path / "credentials.lock"
    assert path.exists()
    identity = path.stat().st_ino
    for _ in range(50):
        Auth(tmp_path).info()
        assert path.stat().st_ino == identity

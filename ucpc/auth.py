"""Official Sign in with ChatGPT flow. UCPC owns its own registration and tokens."""

import base64
import hashlib
import json
import os
import secrets
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import jwt
from filelock import FileLock

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + PLAN_SCOPE
USAGE_URL = "https://chatgpt.com/settings/usage"


class LoginCancelled(RuntimeError):
    def __init__(self):
        super().__init__("Вхід скасовано. Можна спробувати знову.")


class LoopbackServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        # A local connection without HTTP headers must not freeze cancellation.
        connection.settimeout(0.25)
        return connection, address


def check_cancelled(cancel: Event | None):
    if cancel is not None and cancel.is_set():
        raise LoginCancelled()


def state_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "UCPC"


def validate_callback(
    query: dict[str, list[str]], state: str, registered_client: str | None
) -> tuple[str, str]:
    def value(name):
        values = query.get(name, [])
        if len(values) > 1:
            raise ValueError("Повторений параметр входу")
        return values[0] if len(values) == 1 else ""

    if not secrets.compare_digest(value("state").encode(), state.encode()):
        raise ValueError("Неправильний state входу")
    if value("error") == "3p_login_workspace_scope_denied":
        raise ValueError(
            "Це підключення належить іншому акаунту або робочому простору. "
            "Скасуй вхід і натисни «Інший акаунт / робочий простір» у UCPC."
        )
    if value("error"):
        raise ValueError("Вхід не дозволено або скасовано")
    client = value("client_id") or registered_client
    if not client or client == "dynamic_agent_client":
        raise ValueError("OpenAI не повернув виданий client_id")
    if registered_client and client != registered_client:
        raise ValueError("Вхід повернув client_id іншого акаунта")
    code = value("code")
    if not code:
        raise ValueError("OpenAI не повернув код входу")
    return code, client


def validate_identity(token: str, client_id: str, nonce: str | None = None) -> dict:
    key = jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json", timeout=20)
    claims = jwt.decode(
        token,
        key.get_signing_key_from_jwt(token).key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=ISSUER,
        options={"require": ["sub", "exp", "iss", "aud"]},
    )
    if nonce is not None:
        returned_nonce = claims.get("nonce", "")
        if not isinstance(returned_nonce, str) or not secrets.compare_digest(
            returned_nonce.encode(), nonce.encode()
        ):
            raise ValueError("Неправильний nonce входу")
    return claims


def token_request(data: dict) -> dict:
    response = httpx.post(TOKEN_URL, data=data, timeout=30)
    if response.status_code != 200:
        # Token endpoint bodies and URLs never enter logs or UI.
        raise RuntimeError(f"OpenAI OAuth: HTTP {response.status_code}. Спробуй увійти знову.")
    return response.json()


class Auth:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or state_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "account.dat"
        # Keep a stable native lock file; deleting it after every release races with
        # concurrent Windows opens under load. The byte-range lock still releases.
        self.lock = FileLock(
            str(self.directory / "credentials.lock"), timeout=30, preserve_lock_file=True
        )

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        import win32crypt

        try:
            plain = win32crypt.CryptUnprotectData(self.path.read_bytes(), None, None, None, 1)[1]
            return json.loads(plain)
        except Exception:  # noqa: BLE001 — suppress credential contents in error reporting.
            raise RuntimeError("Не вдалося прочитати вхід UCPC для цього користувача Windows.")

    def _save(self, record: dict) -> None:
        import win32crypt

        encrypted = win32crypt.CryptProtectData(
            json.dumps(record).encode(), "UCPC ChatGPT", None, None, None, 1
        )
        temp = self.path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_bytes(encrypted)
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def host_id(self) -> str:
        with self.lock:
            path = self.directory / "host.json"
            if not path.exists():
                path.write_text(
                    json.dumps({"id": "urn:uuid:" + str(uuid.uuid4())}), encoding="utf-8"
                )
            return json.loads(path.read_text(encoding="utf-8"))["id"]

    def info(self) -> str:
        with self.lock:
            record = self._load()
            if not record.get("access_token"):
                return "ChatGPT: вхід потрібен"
            return "ChatGPT: " + record.get("email", "підключено")

    def accounts(self) -> list[dict]:
        """Return display metadata only; every registration keeps its own credentials."""
        with self.lock:
            active = self._load()
        registrations = dict(active.get("registrations", {}))
        if active.get("client_id"):
            registrations[active["client_id"]] = active
        return [
            {
                "id": client,
                "label": (record.get("email") or "Акаунт ChatGPT") + " · " + client[-8:],
                "active": client == active.get("client_id"),
            }
            for client, record in registrations.items()
        ]

    def login(
        self, timeout: float = 180, cancel: Event | None = None, on_ready=None,
        *, new_account: bool = False, account_id: str | None = None,
    ) -> str:
        if new_account and account_id is not None:
            raise ValueError("Обери збережений акаунт або нове підключення")
        check_cancelled(cancel)
        with self.lock:
            initial = self._load()
        if new_account:
            old = {}
        elif account_id is None or account_id == initial.get("client_id"):
            old = initial
        else:
            old = initial.get("registrations", {}).get(account_id)
            if old is None:
                raise ValueError("Збережене підключення не знайдено. Додай інший акаунт.")
        client_id = old.get("client_id")
        host = self.host_id()
        state, nonce, verifier = [secrets.token_urlsafe(32) for _ in range(3)]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        result = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # callback URLs contain a one-time code; never log them

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != "/auth/callback":
                    self.send_error(404)
                    return
                query = parse_qs(parsed.query)
                try:
                    code, issued = validate_callback(query, state, client_id)
                except ValueError as exc:
                    if query.get("state") == [state]:
                        result["error"] = str(exc)
                    self.send_error(400, "Sign-in rejected")
                    return
                result.update(code=code, client_id=issued)
                body = b"UCPC is finishing sign-in. You can close this tab and return to UCPC."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with LoopbackServer(("127.0.0.1", 0), Handler) as server:
            server.timeout = 0.1
            redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
            params = {
                "client_id": client_id or "dynamic_agent_client",
                "ext_agent_host_id": host,
                "response_type": "code",
                "redirect_uri": redirect,
                "scope": SCOPES,
                "resource": RESOURCE,
                "state": state,
                "nonce": nonce,
                "code_challenge_method": "S256",
                "code_challenge": challenge.decode().rstrip("="),
            }
            if client_id:
                if old.get("id_token"):
                    params["id_token_hint"] = old["id_token"]
                if old.get("email"):
                    params["login_hint"] = old["email"]
            else:
                params["agent_name_hint"] = "UCPC"
            url = ISSUER + "/api/accounts/authorize?" + urlencode(params)
            check_cancelled(cancel)
            if not webbrowser.open(url):
                raise RuntimeError("Не вдалося відкрити браузер для входу")
            if on_ready is not None:
                on_ready(url)
            deadline = time.monotonic() + timeout
            while not result and time.monotonic() < deadline:
                check_cancelled(cancel)
                server.handle_request()
        check_cancelled(cancel)
        if "error" in result:
            raise RuntimeError(result["error"])
        if not result:
            raise RuntimeError("Час входу минув. Спробуй ще раз.")
        issued = result["client_id"]
        tokens = token_request(
            {
                "grant_type": "authorization_code",
                "client_id": issued,
                "code": result["code"],
                "code_verifier": verifier,
                "redirect_uri": redirect,
                "resource": RESOURCE,
            }
        )
        check_cancelled(cancel)
        identity = validate_identity(tokens["id_token"], issued, nonce)
        if old.get("subject") and identity["sub"] != old["subject"]:
            raise RuntimeError("Вхід повернув інший акаунт")
        scopes = tokens.get("scope", "").split()
        if PLAN_SCOPE not in scopes:
            raise RuntimeError("Не надано дозвіл використовувати підписку ChatGPT")
        record = dict(
            tokens,
            client_id=issued,
            subject=identity["sub"],
            email=identity.get("email", ""),
            ext_agent_host_id=host,
            scopes=scopes,
            expires_at=time.time() + tokens["expires_in"],
        )
        with self.lock:
            check_cancelled(cancel)
            latest = self._load()
            if latest.get("client_id") != initial.get("client_id"):
                raise RuntimeError("Активний акаунт уже змінився. Повтори вхід.")
            registrations = dict(latest.get("registrations", {}))
            if latest.get("client_id"):
                registrations[latest["client_id"]] = {
                    key: value for key, value in latest.items() if key != "registrations"
                }
            previous = registrations.pop(issued, {})
            if previous.get("subject") and previous["subject"] != identity["sub"]:
                raise RuntimeError("Виданий client_id належить іншому акаунту")
            if registrations:
                record["registrations"] = registrations
            self._save(record)
        return record["email"]

    def access_token(self, *, min_validity: float = 60, rejected_token: str | None = None) -> str:
        with self.lock:
            record = self._load()
            if not record.get("access_token"):
                raise RuntimeError("Спочатку обери Continue with ChatGPT у меню UCPC")
            if (
                record["expires_at"] < time.time() + min_validity
                or (rejected_token is not None and record["access_token"] == rejected_token)
            ):
                if not record.get("refresh_token"):
                    raise RuntimeError("Потрібен повторний вхід у ChatGPT")
                tokens = token_request(
                    {
                        "grant_type": "refresh_token",
                        "client_id": record["client_id"],
                        "refresh_token": record["refresh_token"],
                        "resource": RESOURCE,
                    }
                )
                if tokens.get("id_token"):
                    identity = validate_identity(tokens["id_token"], record["client_id"])
                    if identity["sub"] != record["subject"]:
                        raise RuntimeError("Оновлення входу повернуло інший акаунт")
                scopes = tokens.get("scope", " ".join(record["scopes"])).split()
                if PLAN_SCOPE not in scopes:
                    raise RuntimeError("Дозвіл на підписку ChatGPT відкликано")
                record.update(tokens, scopes=scopes, expires_at=time.time() + tokens["expires_in"])
                self._save(record)
            return record["access_token"]

    def models(self) -> list[dict]:
        token = self.access_token()
        response = httpx.get(
            RESOURCE + "/models", headers={"Authorization": "Bearer " + token}, timeout=30
        )
        if response.status_code != 200:
            raise RuntimeError(f"Каталог моделей: HTTP {response.status_code}")
        return [
            model
            for model in response.json().get("models", [])
            if model.get("visibility") == "list"
        ]

    def logout(self) -> bool:
        with self.lock:
            record = self._load()
            confirmed = True
            if record.get("refresh_token"):
                try:
                    response = httpx.post(
                        ISSUER + "/api/accounts/oauth/revoke",
                        data={
                            "token": record["refresh_token"],
                            "token_type_hint": "refresh_token",
                            "client_id": record["client_id"],
                        },
                        timeout=20,
                    )
                    confirmed = response.status_code == 200
                except httpx.HTTPError:
                    confirmed = False
            for key in ("access_token", "refresh_token", "id_token"):
                record.pop(key, None)
            self._save(record)
            return confirmed

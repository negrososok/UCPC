import queue
import socket
import threading
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

from ucpc.auth import PLAN_SCOPE, Auth, LoginCancelled, LoopbackServer


@pytest.fixture
def login_harness(tmp_path, monkeypatch):
    opened = queue.Queue()
    auth = Auth(tmp_path)
    servers = []

    class ObservedServer(LoopbackServer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            servers.append(self)

    monkeypatch.setattr("ucpc.auth.LoopbackServer", ObservedServer)
    auth.test_servers = servers
    monkeypatch.setattr("ucpc.auth.webbrowser.open", lambda url: opened.put(url) or True)
    exchanges = []

    def tokens(data):
        exchanges.append(data)
        return {
            "id_token": "test-id",
            "access_token": "test-access",
            "refresh_token": "test-refresh",
            "expires_in": 3600,
            "scope": PLAN_SCOPE,
        }

    monkeypatch.setattr("ucpc.auth.token_request", tokens)
    monkeypatch.setattr(
        "ucpc.auth.validate_identity",
        lambda *args: {"sub": "test-user", "email": "test@example.invalid"},
    )
    return auth, opened, exchanges


def start_login(auth, **kwargs):
    result = []

    def run():
        try:
            result.append(auth.login(**kwargs))
        except Exception as exc:  # noqa: BLE001 — tests inspect worker errors
            result.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, result


def callback(url, **overrides):
    params = parse_qs(urlparse(url).query)
    data = {"state": params["state"][0], "code": "test-code", "client_id": "oaiapp_test"}
    data.update(overrides)
    return params["redirect_uri"][0] + "?" + urlencode(data)


def test_closed_tab_cancel_and_retry_real_loopback(login_harness):
    auth, opened, exchanges = login_harness
    cancel = threading.Event()
    first, result = start_login(auth, cancel=cancel)
    first_url = opened.get(timeout=2)
    cancel.set()  # closing the tab has no callback; user explicitly cancels
    first.join(timeout=1)
    assert not first.is_alive()
    assert isinstance(result[0], LoginCancelled)
    assert not exchanges
    second, result = start_login(auth)
    second_url = opened.get(timeout=2)
    first_state = parse_qs(urlparse(first_url).query)["state"][0]
    assert httpx.get(callback(second_url, state=first_state), timeout=2).status_code == 400
    assert second.is_alive()
    assert httpx.get(callback(second_url), timeout=2).status_code == 200
    second.join(timeout=2)
    assert result == ["test@example.invalid"]
    assert len(exchanges) == 1
    assert auth._load()["client_id"] == "oaiapp_test"
    assert auth.test_servers[0].socket.fileno() == -1


def test_timeout_releases_listener_and_allows_retry(login_harness):
    auth, opened, _ = login_harness
    worker, result = start_login(auth, timeout=0.15)
    opened.get(timeout=2)
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert "Час входу минув" in str(result[0])
    assert auth.test_servers[0].socket.fileno() == -1


def test_denied_login_does_not_exchange_tokens(login_harness):
    auth, opened, exchanges = login_harness
    worker, result = start_login(auth)
    url = opened.get(timeout=2)
    assert httpx.get(callback(url, error="access_denied"), timeout=2).status_code == 400
    worker.join(timeout=1)
    assert not exchanges
    assert not auth.path.exists()
    assert "скасовано" in str(result[0])


def test_cancel_during_exchange_cannot_save_tokens(login_harness, monkeypatch):
    auth, opened, _ = login_harness
    cancel = threading.Event()

    def exchange(data):
        cancel.set()
        return {
            "id_token": "test-id",
            "access_token": "secret",
            "expires_in": 3600,
            "scope": PLAN_SCOPE,
        }

    monkeypatch.setattr("ucpc.auth.token_request", exchange)
    worker, result = start_login(auth, cancel=cancel)
    url = opened.get(timeout=2)
    httpx.get(callback(url), timeout=2)
    worker.join(timeout=1)
    assert isinstance(result[0], LoginCancelled)
    assert not auth.path.exists()


def test_browser_failure_is_recoverable(login_harness, monkeypatch):
    auth, _, _ = login_harness
    monkeypatch.setattr("ucpc.auth.webbrowser.open", lambda url: False)
    with pytest.raises(RuntimeError, match="відкрити браузер"):
        auth.login()
    assert not auth.path.exists()


def test_incomplete_local_http_connection_does_not_block_cancel(login_harness):
    auth, opened, _ = login_harness
    cancel = threading.Event()
    worker, result = start_login(auth, cancel=cancel)
    params = parse_qs(urlparse(opened.get(timeout=2)).query)
    listener = urlparse(params["redirect_uri"][0])
    with socket.create_connection((listener.hostname, listener.port), timeout=1) as connection:
        connection.sendall(b"GET /auth/callback HTTP/1.1\r\n")
        cancel.set()
        worker.join(timeout=1)
    assert not worker.is_alive()
    assert isinstance(result[0], LoginCancelled)


@pytest.mark.stress
def test_30_cancelled_logins_release_every_worker_and_listener(login_harness):
    auth, opened, exchanges = login_harness
    for _ in range(30):
        cancel = threading.Event()
        worker, result = start_login(auth, cancel=cancel)
        opened.get(timeout=2)
        cancel.set()
        worker.join(timeout=1)
        assert not worker.is_alive() and isinstance(result[0], LoginCancelled)
    assert not exchanges and not auth.path.exists()
    assert all(server.socket.fileno() == -1 for server in auth.test_servers)


@pytest.mark.stress
def test_100_invalid_callbacks_cannot_finish_or_poison_login(login_harness):
    auth, opened, exchanges = login_harness
    worker, result = start_login(auth)
    url = opened.get(timeout=2)
    with httpx.Client(timeout=2) as client:
        for i in range(100):
            assert client.get(callback(url, state=f"invalid-state-{i}")).status_code == 400
        assert not exchanges and worker.is_alive()
        assert client.get(callback(url)).status_code == 200
    worker.join(timeout=2)
    assert not worker.is_alive() and result == ["test@example.invalid"]
    assert len(exchanges) == 1

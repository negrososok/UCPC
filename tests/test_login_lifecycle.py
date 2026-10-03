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


def saved_account(client="oaiapp_first", subject="first-user"):
    return {
        "client_id": client, "subject": subject, "email": "same@example.invalid",
        "access_token": "first-access", "refresh_token": "first-refresh",
        "id_token": "first-id", "scopes": [PLAN_SCOPE], "expires_at": 0,
    }


def finish_login(auth, opened, **options):
    worker, result = start_login(auth, **options)
    url = opened.get(timeout=2)
    assert httpx.get(callback(url), timeout=2).status_code == 200
    worker.join(timeout=2)
    assert not worker.is_alive() and result == ["test@example.invalid"]
    return parse_qs(urlparse(url).query)


def test_different_account_registers_fresh_and_keeps_old_mapping(login_harness):
    auth, opened, exchanges = login_harness
    old = saved_account()
    auth._save(old)
    host = auth.host_id()
    query = finish_login(auth, opened, new_account=True)
    assert query["client_id"] == ["dynamic_agent_client"]
    assert query["agent_name_hint"] == ["UCPC"]
    assert query["ext_agent_host_id"] == [host]
    assert "id_token_hint" not in query and "login_hint" not in query
    assert exchanges[0]["client_id"] == "oaiapp_test"
    record = auth._load()
    assert record["client_id"] == "oaiapp_test" and record["subject"] == "test-user"
    assert record["registrations"] == {"oaiapp_first": old}
    assert b"first-refresh" not in auth.path.read_bytes()
    assert b"test-refresh" not in auth.path.read_bytes()
    assert all(set(account) == {"id", "label", "active"} for account in auth.accounts())


def test_logged_out_old_account_does_not_block_different_account(login_harness, monkeypatch):
    auth, opened, _ = login_harness
    auth._save(saved_account())
    monkeypatch.setattr("ucpc.auth.httpx.post", lambda *a, **kw: httpx.Response(200))
    assert auth.logout()
    query = finish_login(auth, opened, new_account=True)
    assert query["client_id"] == ["dynamic_agent_client"]
    assert auth._load()["registrations"]["oaiapp_first"]["subject"] == "first-user"
    assert "access_token" not in auth._load()["registrations"]["oaiapp_first"]


def test_saved_registration_reuses_own_client_and_identity_hints(login_harness):
    auth, opened, exchanges = login_harness
    old = saved_account("oaiapp_test", "test-user")
    active = saved_account()
    active["registrations"] = {"oaiapp_test": old}
    auth._save(active)
    query = finish_login(auth, opened, account_id="oaiapp_test")
    assert query["client_id"] == ["oaiapp_test"]
    assert query["id_token_hint"] == ["first-id"]
    assert query["login_hint"] == ["same@example.invalid"]
    assert "agent_name_hint" not in query
    assert exchanges[0]["client_id"] == "oaiapp_test"
    assert auth._load()["registrations"]["oaiapp_first"]["subject"] == "first-user"
    assert "oaiapp_test" not in auth._load()["registrations"]


def test_returning_login_keeps_client_after_logout(login_harness, monkeypatch):
    auth, opened, _ = login_harness
    auth._save(saved_account("oaiapp_test", "test-user"))
    monkeypatch.setattr("ucpc.auth.httpx.post", lambda *a, **kw: httpx.Response(200))
    auth.logout()
    query = finish_login(auth, opened)
    assert query["client_id"] == ["oaiapp_test"]
    assert "agent_name_hint" not in query and "id_token_hint" not in query


@pytest.mark.parametrize("failure", ["denied", "scope", "cancel", "identity"])
def test_failed_account_switch_preserves_current_encrypted_credentials(
    login_harness, monkeypatch, failure,
):
    auth, opened, exchanges = login_harness
    old = saved_account()
    auth._save(old)
    encrypted = auth.path.read_bytes()
    cancel = threading.Event()
    if failure == "scope":
        monkeypatch.setattr("ucpc.auth.token_request", lambda _: {
            "id_token": "synthetic", "scope": "openid", "expires_in": 3600,
        })
    elif failure == "identity":
        # A newly issued client may not steal another stored registration's identity.
        auth._save(saved_account("oaiapp_test", "different-user"))
        old, encrypted = auth._load(), auth.path.read_bytes()
    worker, result = start_login(auth, new_account=True, cancel=cancel)
    url = opened.get(timeout=2)
    if failure == "cancel":
        cancel.set()
    else:
        overrides = {"error": "3p_login_workspace_scope_denied"} if failure == "denied" else {}
        httpx.get(callback(url, **overrides), timeout=2)
    worker.join(timeout=2)
    assert not worker.is_alive() and isinstance(result[0], Exception)
    assert auth.path.read_bytes() == encrypted and auth._load() == old
    if failure == "denied":
        assert not exchanges and "Інший акаунт" in str(result[0])


def test_stale_login_cannot_replace_an_account_selected_by_another_worker(login_harness):
    auth, opened, _ = login_harness
    auth._save(saved_account())
    worker, result = start_login(auth, new_account=True)
    url = opened.get(timeout=2)
    newer = saved_account("oaiapp_newer", "newer-user")
    auth._save(newer)
    httpx.get(callback(url), timeout=2)
    worker.join(timeout=2)
    assert not worker.is_alive() and "уже змінився" in str(result[0])
    assert auth._load() == newer


def test_same_email_registrations_have_distinct_display_labels(login_harness):
    auth, _, _ = login_harness
    active = saved_account()
    active["registrations"] = {"oaiapp_second": saved_account("oaiapp_second", "other-user")}
    auth._save(active)
    accounts = auth.accounts()
    assert len(accounts) == 2 and len({account["label"] for account in accounts}) == 2
    assert [account["id"] for account in accounts if account["active"]] == ["oaiapp_first"]


def test_switch_keeps_concurrently_rotated_credentials_for_previous_account(login_harness):
    auth, opened, _ = login_harness
    auth._save(saved_account())
    worker, result = start_login(auth, new_account=True)
    url = opened.get(timeout=2)
    rotated = saved_account()
    rotated.update(access_token="rotated-access", refresh_token="rotated-refresh")
    auth._save(rotated)
    httpx.get(callback(url), timeout=2)
    worker.join(timeout=2)
    assert result == ["test@example.invalid"]
    assert auth._load()["registrations"]["oaiapp_first"] == rotated


def test_logout_revokes_only_active_account_and_retains_other_registration(
    login_harness, monkeypatch,
):
    auth, _, _ = login_harness
    other = saved_account("oaiapp_other", "other-user")
    active = saved_account()
    active["registrations"] = {"oaiapp_other": other}
    auth._save(active)
    requests = []
    monkeypatch.setattr("ucpc.auth.httpx.post", lambda *a, **kw: (
        requests.append(kw["data"]) or httpx.Response(200)
    ))
    assert auth.logout()
    assert requests[0]["client_id"] == "oaiapp_first"
    assert requests[0]["token"] == "first-refresh"
    assert "access_token" not in auth._load()
    assert auth._load()["registrations"]["oaiapp_other"] == other

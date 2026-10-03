"""Two-pass publication, API SSE errors, reasoning and cancellation regressions."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest
from openai import AsyncOpenAI

from ucpc.auth import PLAN_SCOPE, Auth
from ucpc.config import Config
from ucpc.engine import Engine
from ucpc.history import Track


def stream_response(text):
    events = [
        {"type": "response.output_text.delta", "delta": text},
        {"type": "response.completed", "response": {"id": "test", "model": "gpt-6-astra"}},
    ]
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content="".join("data: " + json.dumps(e) + "\n\n" for e in events),
    )


@pytest.fixture
def solver_job(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")

    def make(handler, verify=True):
        def client(**kwargs):
            return AsyncOpenAI(
                **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
            )

        monkeypatch.setattr("ucpc.engine.AsyncOpenAI", client)
        job = Engine.__new__(Engine)
        job.config = Config(auth_mode="api", api_vision_model="gpt-6-astra", verify_answer=verify)
        job.auth = None
        errors = []
        job.on_error = lambda *args: errors.append(args)
        return job, errors

    return make


async def generate(job, track):
    await job._generate(
        ("data:image/png;base64,first", "data:image/png;base64,second"),
        "Synthetic personal instruction",
        track,
    )


@pytest.mark.asyncio
async def test_draft_stays_hidden_and_review_keeps_original_images_and_personal_prompt(solver_job):
    track = Track()
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert track.snapshot()[0] == ""  # Both stages stay hidden until final completion.
        if len(requests) == 1:
            return stream_response("INCORRECT_DRAFT")
        assert "INCORRECT_DRAFT" in body["input"][1]["content"][0]["text"]
        return stream_response("CORRECTED_CODE")

    job, errors = solver_job(handle)
    await generate(job, track)
    assert not errors and track.snapshot() == ("CORRECTED_CODE", True, "")
    assert len(requests) == 2
    assert requests[0]["input"][0] == requests[1]["input"][0]
    assert requests[0]["instructions"] != requests[1]["instructions"]
    for payload in requests:
        assert payload["instructions"].startswith("Synthetic personal instruction")
        assert payload["reasoning"] == {"effort": "high"}
        assert payload["store"] is False
        assert all(
            part["detail"] == "high"
            for part in payload["input"][0]["content"]
            if part["type"] == "input_image"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code, phrase",
    [
        ("server_is_overloaded", "тимчасово"),
        ("subscription_sharing_usage_limit_exceeded", "ліміт"),
        ("model_not_available", "недоступна"),
        ("unknown_stream_failure", "перервав"),
    ],
)
async def test_sdk_sse_error_is_classified_without_exposing_server_message(
    solver_job, code, phrase
):
    body = {"error": {"code": code, "message": "PRIVATE_SERVER_MESSAGE"}}
    job, errors = solver_job(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="data: " + json.dumps(body) + "\n\n",
        )
    )
    track = Track()
    await generate(job, track)
    assert len(errors) == 1 and track.snapshot()[1]
    assert phrase in track.snapshot()[2] and "PRIVATE" not in track.snapshot()[2]


@pytest.mark.asyncio
async def test_failed_review_never_publishes_unverified_draft(solver_job):
    calls = []

    def handle(request):
        calls.append(1)
        if len(calls) == 1:
            return stream_response("UNVERIFIED_CODE")
        return httpx.Response(503, json={"error": {"code": "server_is_overloaded"}})

    job, errors = solver_job(handle)
    track = Track()
    await generate(job, track)
    assert len(calls) == 2 and len(errors) == 1
    assert track.snapshot()[0] == "" and track.snapshot()[1]


@pytest.mark.asyncio
async def test_cancel_during_review_publishes_no_partial_code(solver_job):
    calls = []
    started = asyncio.Event()

    class Waiting(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'data: {"type":"response.output_text.delta","delta":"UNVERIFIED"}\n\n'
            started.set()
            await asyncio.sleep(60)

        async def aclose(self):
            self.closed = True

    stream = Waiting()

    def handle(request):
        calls.append(1)
        return (
            stream_response("DRAFT")
            if len(calls) == 1
            else httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        )

    job, errors = solver_job(handle)
    track = Track()
    task = asyncio.create_task(generate(job, track))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert track.snapshot() == ("", True, "Скасовано") and stream.closed and not errors


@pytest.mark.asyncio
async def test_disabled_verification_uses_single_request(solver_job):
    requests = []

    def handle(request):
        requests.append(1)
        return stream_response("DIRECT_RESPONSE")

    job, errors = solver_job(handle, verify=False)
    track = Track()
    await generate(job, track)
    assert requests == [1] and not errors
    assert track.snapshot() == ("DIRECT_RESPONSE", True, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("source, expected", [
    ("int main() {\n    return 0;\n}\n", "int main() {\n\treturn 0;\n}\n"),
    ("for i in range(3):\n    print(i)\n", "for i in range(3):\n    print(i)\n"),
    ("import sys\nfrom collections import deque\nwhile True:\n\tbreak\n",
     "import sys\nfrom collections import deque\nwhile True:\n    break\n"),
])
async def test_final_code_style_keeps_language_and_single_request(solver_job, source, expected):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return stream_response(source)

    job, errors = solver_job(handle, verify=False)
    track = Track()
    await generate(job, track)
    assert not errors and track.snapshot() == (expected, True, "")
    assert len(requests) == 1
    prompt = requests[0]["instructions"]
    assert prompt.startswith("Synthetic personal instruction")
    assert "Python" in prompt and "JavaScript" in prompt and "Rust" in prompt
    assert "без табів і змішаних відступів" in prompt
    assert "без потреби" in prompt and "занадто повільним" in prompt
    assert requests[0]["reasoning"] == {"effort": "high"}


@pytest.mark.asyncio
async def test_incomplete_code_keeps_original_partial_text(solver_job):
    source = "int main() {\n    return 0;\n"
    event = {"type": "response.output_text.delta", "delta": source}
    job, errors = solver_job(lambda _: httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        content="data: " + json.dumps(event) + "\n\n"
    ), verify=False)
    track = Track()
    await generate(job, track)
    assert len(errors) == 1 and track.snapshot()[0] == source
    assert track.snapshot()[1] and track.snapshot()[2]


@pytest.mark.asyncio
async def test_explicit_run_verification_uses_one_call_with_labeled_condition_code_and_result(solver_job):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return stream_response("НЕ ЗБІГАЄТЬСЯ: очікувано 3, отримано 4")

    job, errors = solver_job(handle, verify=True)  # Explicit checking never starts two passes.
    track = Track(kind="verification", candidate="CANDIDATE_CODE", task_images=("ORIGINAL_FRAME",))
    await job._generate(("ORIGINAL_FRAME", "RESULT_FRAME"), "personal instruction", track)
    assert not errors and len(requests) == 1 and track.snapshot()[1]
    assert "ПЕРЕВІРКИ РЕЗУЛЬТАТУ" in requests[0]["instructions"]
    content = requests[0]["input"][0]["content"]
    assert len(requests[0]["input"]) == 1
    assert [p["image_url"] for p in content if p["type"] == "input_image"] == [
        "ORIGINAL_FRAME", "RESULT_FRAME"]
    assert "ПОЧАТКОВА УМОВА" in content[0]["text"]
    assert "CANDIDATE_CODE" in content[2]["text"]
    assert "СВІЖИЙ ЕКРАН" in content[3]["text"]


def oauth_auth(tmp_path, monkeypatch):
    auth = Auth(tmp_path)
    auth._save({"client_id": "test", "subject": "test", "access_token": "old",
                "refresh_token": "refresh", "scopes": [PLAN_SCOPE], "expires_at": 10000})
    monkeypatch.setattr(auth, "models", lambda: [{"slug": "gpt-6-astra"}])
    monkeypatch.setattr("ucpc.auth.time.time", lambda: 1000)
    refresh = Mock(return_value={"access_token": "new", "refresh_token": "rotated",
                                 "expires_in": 3600})
    monkeypatch.setattr("ucpc.auth.token_request", refresh)
    return auth, refresh


@pytest.mark.asyncio
async def test_expiry_between_passes_uses_fresh_token_without_repeating_draft(
    solver_job, tmp_path, monkeypatch
):
    auth, refresh = oauth_auth(tmp_path, monkeypatch)
    tokens = []

    def handle(request):
        tokens.append(request.headers["authorization"])
        if len(tokens) == 1:
            monkeypatch.setattr("ucpc.auth.time.time", lambda: 11000)
            return stream_response("DRAFT")
        return stream_response("CHECKED")

    job, errors = solver_job(handle)
    job.config = replace(job.config, auth_mode="chatgpt", vision_model="gpt-6-astra")
    job.auth = auth
    track = Track()
    await generate(job, track)
    assert tokens == ["Bearer old", "Bearer new"] and refresh.call_count == 1
    assert track.snapshot() == ("CHECKED", True, "") and not errors


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code, status, expected_calls", [
    ("token_expired", 401, 3), ("token_expired", 200, 3),
    ("subscription_sharing_usage_limit_exceeded", 429, 2), ("permission_denied", 403, 2),
])
async def test_only_expired_oauth_admission_retries_review_once(
    solver_job, tmp_path, monkeypatch, error_code, status, expected_calls
):
    auth, refresh = oauth_auth(tmp_path, monkeypatch)
    bodies = []
    tokens = []

    def handle(request):
        bodies.append(json.loads(request.content))
        tokens.append(request.headers["authorization"])
        if len(bodies) == 1:
            return stream_response("DRAFT")
        if len(bodies) == 2:
            error = {"error": {"code": error_code, "message": "PRIVATE_MESSAGE"}}
            return (httpx.Response(status, json=error) if status != 200 else
                    httpx.Response(200, headers={"content-type": "text/event-stream"},
                                   content="data: " + json.dumps(error) + "\n\n"))
        return stream_response("CHECKED")

    job, errors = solver_job(handle)
    job.config = replace(job.config, auth_mode="chatgpt", vision_model="gpt-6-astra")
    job.auth = auth
    track = Track()
    await generate(job, track)
    assert len(bodies) == expected_calls
    if error_code == "token_expired":
        assert bodies[1] == bodies[2] and tokens == ["Bearer old", "Bearer old", "Bearer new"]
        assert refresh.call_count == 1 and not errors
        assert track.snapshot() == ("CHECKED", True, "")
    else:
        assert not refresh.called and len(errors) == 1 and track.snapshot()[0] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_expired_token_recovery_is_bounded_and_never_replays_partial_text(
    solver_job, tmp_path, monkeypatch, partial
):
    auth, refresh = oauth_auth(tmp_path, monkeypatch)
    requests = []

    def handle(request):
        requests.append(request)
        error = {"error": {"code": "token_expired", "message": "PRIVATE_TOKEN_ERROR"}}
        if partial:
            events = [{"type": "response.output_text.delta", "delta": "PARTIAL"}, error]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content="".join("data: " + json.dumps(e) + "\n\n" for e in events))
        return httpx.Response(401, json=error)

    job, errors = solver_job(handle, verify=False)
    job.config = replace(job.config, auth_mode="chatgpt", vision_model="gpt-6-astra")
    job.auth = auth
    track = Track()
    await generate(job, track)
    assert len(requests) == (1 if partial else 2)
    assert refresh.call_count == (0 if partial else 1)
    assert len(errors) == 1 and track.snapshot()[1]
    assert track.snapshot()[0] == ("PARTIAL" if partial else "")
    assert "PRIVATE" not in track.snapshot()[2]


@pytest.mark.asyncio
@pytest.mark.stress
async def test_two_hundred_new_conversations_have_no_previous_context(solver_job):
    current = 0
    calls = 0
    histories = []

    def handle(request):
        nonlocal calls
        payload = json.loads(request.content)
        assert "previous_response_id" not in payload and "conversation" not in payload
        assert payload["store"] is False
        content = payload["input"][0]["content"]
        images = [part["image_url"] for part in content if part["type"] == "input_image"]
        assert images == [f"data:image/png;base64,TASK_{current}_FRAME_{i}" for i in range(3)]
        if calls % 2:
            assert len(payload["input"]) == 2
            assert payload["input"][1]["content"][0]["text"] == (
                f"<unverified_draft>\nDRAFT_{current}\n</unverified_draft>"
            )
        else:
            assert len(payload["input"]) == 1
        assert all(f"ANSWER_{i}" not in json.dumps(payload) for i in range(current))
        calls += 1
        return stream_response(f"{'DRAFT' if calls % 2 else 'ANSWER'}_{current}")

    job, errors = solver_job(handle)
    for current in range(200):
        track = Track()
        await job._generate(
            tuple(f"data:image/png;base64,TASK_{current}_FRAME_{i}" for i in range(3)),
            "Synthetic personal instruction", track,
        )
        assert track.snapshot() == (f"ANSWER_{current}", True, "")
        histories.append(track)
    assert calls == 400 and len(histories) == 200 and not errors

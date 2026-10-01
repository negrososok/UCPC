"""Exercise the real OpenAI SDK against a local in-memory HTTP transport."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI

from ucpc.config import Config
from ucpc.engine import Engine
from ucpc.history import Track


@pytest.fixture
def transport_job(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    requests = []
    errors = []

    async def run(handler, config=None, auth=None):
        def handle(request):
            requests.append(json.loads(request.content))
            return handler(request)

        def client(**kwargs):
            return AsyncOpenAI(
                **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
            )

        monkeypatch.setattr("ucpc.engine.AsyncOpenAI", client)
        job = Engine.__new__(Engine)
        job.config = config or Config(auth_mode="api")
        job.auth = auth
        job.on_error = lambda *args: errors.append(args)
        track = Track()
        await job._generate("data:image/png;base64,synthetic", "Test instructions", track)
        return track

    return run, requests, errors


def sse(events):
    body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["api", "chatgpt"])
async def test_real_sdk_success_and_correct_subscription_payload(transport_job, mode):
    run, requests, errors = transport_job
    auth = SimpleNamespace(
        access_token=lambda: "synthetic-oauth", models=lambda: [{"slug": "test"}]
    )
    track = await run(
        lambda _: sse(
            [
                {"type": "response.output_text.delta", "delta": "Перше. Друге."},
                {
                    "type": "response.completed",
                    "response": {"id": "test", "model": "actual-test-model"},
                },
            ]
        ),
        Config(auth_mode=mode, vision_model="auto"),
        auth,
    )
    assert not errors
    text, complete, error = track.snapshot()
    assert text == "Перше. Друге." and complete and not error
    assert track.model_name() == "actual-test-model"
    assert requests[0]["store"] is False and requests[0]["stream"] is True
    assert ("max_output_tokens" in requests[0]) == (mode == "api")
    assert requests[0]["input"][0]["content"][1]["type"] == "input_image"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_http_failures_finish_track_and_report_once(transport_job, status):
    run, requests, errors = transport_job
    track = await run(lambda _: httpx.Response(status, json={"error": {"message": "private"}}))
    assert len(requests) == 1  # retries are deliberately disabled
    assert len(errors) == 1 and errors[0][0] is track
    assert track.snapshot()[1] and track.snapshot()[2]
    assert "private" not in track.snapshot()[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_disconnect_and_timeout(transport_job, failure):
    run, _, errors = transport_job

    def fail(request):
        raise failure("synthetic transport failure", request=request)

    track = await run(fail)
    assert len(errors) == 1 and track.snapshot()[1]
    assert (
        "вчасно" in track.snapshot()[2]
        if failure is httpx.ReadTimeout
        else "з’єднання" in track.snapshot()[2]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events", [[], [{"type": "response.completed", "response": {"id": "test"}}]]
)
async def test_empty_or_interrupted_response_cannot_be_success(transport_job, events):
    run, _, errors = transport_job
    track = await run(lambda _: sse(events))
    assert len(errors) == 1 and track.snapshot()[1] and track.snapshot()[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["no_models", "unavailable_model", "missing_key"])
async def test_local_access_errors_never_send_screenshot(transport_job, monkeypatch, fault):
    run, requests, errors = transport_job
    monkeypatch.delenv("OPENAI_API_KEY")
    auth = SimpleNamespace(access_token=lambda: "synthetic", models=lambda: [{"slug": "test"}])
    config = Config(vision_model="auto")
    if fault == "no_models":
        auth.models = list
    elif fault == "unavailable_model":
        config = Config(vision_model="unavailable")
    elif fault == "missing_key":
        config = Config(auth_mode="api")
    track = await run(lambda _: pytest.fail("Unexpected network request"), config, auth)
    assert not requests and len(errors) == 1 and track.snapshot()[1]

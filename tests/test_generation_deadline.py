"""Late first tokens, finite output grace, two passes and cancellation."""

import asyncio
from types import SimpleNamespace

import pytest

from ucpc.config import Config
from ucpc.engine import Engine
from ucpc.history import Track


@pytest.fixture
def job(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setattr(Engine, "TOTAL_TIMEOUT_FLOOR", 0.08)
    monkeypatch.setattr(Engine, "OUTPUT_TIMEOUT", 0.12)
    monkeypatch.setattr(Engine, "SETUP_TIMEOUT", 0.04)

    def make(events, *, budget=0.08, verify=False):
        streams, client_options, errors = [], [], []

        class Stream:
            closed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                self.closed = True

            def __aiter__(self):
                return events()

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def create(self, **_):
                stream = Stream()
                streams.append(stream)
                return stream

            @property
            def responses(self):
                return self

        def client(**kwargs):
            client_options.append(kwargs)
            return Client()

        monkeypatch.setattr("ucpc.engine.AsyncOpenAI", client)
        engine = Engine.__new__(Engine)
        engine.config = Config(auth_mode="api", generation_timeout=budget,
                               request_timeout=0.03, verify_answer=verify)
        engine.auth = None
        engine.on_error = lambda *args: errors.append(args)
        return engine, streams, client_options, errors

    return make


async def generate(engine, track):
    await asyncio.wait_for(engine._generate("synthetic", "synthetic instruction", track), 0.8)


def delta(text="CODE"):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def completed():
    return SimpleNamespace(type="response.completed")


@pytest.mark.asyncio
async def test_late_first_text_gets_time_to_finish_instead_of_cutting_valid_answer(job):
    async def events():
        await asyncio.sleep(0.06)
        yield delta("FIRST")
        await asyncio.sleep(0.05)
        yield delta("_LAST")
        yield completed()

    engine, streams, options, errors = job(events)
    track = Track()
    await generate(engine, track)
    assert track.snapshot() == ("FIRST_LAST", True, "") and not errors
    assert len(options) == 1 and streams[0].closed
    assert options[0]["max_retries"] == 0


@pytest.mark.asyncio
async def test_configured_thinking_budget_is_used_instead_of_old_three_minute_ceiling(job):
    async def events():
        await asyncio.sleep(0.12)  # Beyond the old scaled 0.08 ceiling.
        yield delta()
        yield completed()

    engine, streams, _, errors = job(events, budget=0.24)
    track = Track()
    await generate(engine, track)
    assert track.snapshot() == ("CODE", True, "") and not errors and streams[0].closed


@pytest.mark.asyncio
async def test_early_output_does_not_shorten_existing_budget(job):
    async def events():
        yield delta()
        await asyncio.sleep(0.16)  # Longer than output grace, within original 0.24.
        yield completed()

    engine, streams, _, errors = job(events, budget=0.24)
    track = Track()
    await generate(engine, track)
    assert track.snapshot() == ("CODE", True, "") and not errors and streams[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("send_text", [False, True])
async def test_heartbeats_or_continuous_text_cannot_extend_deadline_forever(job, send_text):
    async def events():
        while True:
            yield delta("x") if send_text else SimpleNamespace(type="response.in_progress")
            await asyncio.sleep(0.003)

    engine, streams, options, errors = job(events)
    track = Track()
    await generate(engine, track)
    text, complete, error = track.snapshot()
    assert complete and "час очікування" in error and len(errors) == len(options) == 1
    assert bool(text) == send_text and streams[0].closed
    if send_text:
        assert "неповний" in error


@pytest.mark.asyncio
async def test_each_optional_review_pass_has_own_output_grace(job):
    async def events():
        await asyncio.sleep(0.06)
        yield delta()
        await asyncio.sleep(0.05)
        yield completed()

    engine, streams, options, errors = job(events, verify=True)
    track = Track()
    await generate(engine, track)
    assert track.snapshot() == ("CODE", True, "") and not errors
    assert len(options) == 2 and all(stream.closed for stream in streams)


@pytest.mark.asyncio
async def test_cancel_during_long_reasoning_still_closes_immediately(job):
    started = asyncio.Event()

    async def events():
        started.set()
        await asyncio.sleep(60)
        yield completed()

    engine, streams, _, errors = job(events, budget=600)
    track = Track()
    task = asyncio.create_task(engine._generate("synthetic", "synthetic", track))
    await asyncio.wait_for(started.wait(), 0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert track.snapshot() == ("", True, "Скасовано") and not errors and streams[0].closed

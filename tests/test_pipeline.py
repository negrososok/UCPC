import asyncio
from types import SimpleNamespace

import pytest

from ucpc.engine import pipeline
from ucpc.history import Track


class Stream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def __aiter__(self):
        for event in self.events:
            await asyncio.sleep(0)
            yield SimpleNamespace(**event)


class Vision:
    def __init__(self, events):
        self.stream = Stream(events)
        self.responses = self

    async def create(self, **kwargs):
        return self.stream


@pytest.mark.asyncio
async def test_streaming_keeps_delta_and_refusal_order():
    vision = Vision(
        [
            {"type": "response.output_text.delta", "delta": "Перше. "},
            {"type": "response.refusal.delta", "delta": "Друге."},
            {"type": "response.completed"},
        ]
    )
    track = Track()
    await pipeline(vision, {}, track)
    assert track.snapshot()[0] == "Перше. Друге."
    assert vision.stream.closed


@pytest.mark.asyncio
async def test_interrupted_stream_preserves_received_text():
    vision = Vision([{"type": "response.output_text.delta", "delta": "Незавершено"}])
    track = Track()
    with pytest.raises(RuntimeError, match="обірвався"):
        await pipeline(vision, {}, track)
    assert track.snapshot()[0] == "Незавершено"
    assert vision.stream.closed


@pytest.mark.asyncio
async def test_cancellation_closes_pending_network_stream():
    class Waiting(Stream):
        async def __aiter__(self):
            yield SimpleNamespace(type="response.output_text.delta", delta="Received")
            await asyncio.sleep(60)

    vision = Vision([])
    vision.stream = Waiting([])
    track = Track()
    task = asyncio.create_task(pipeline(vision, {}, track))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert vision.stream.closed and track.snapshot()[0] == "Received"


@pytest.mark.asyncio
async def test_usage_error_after_partial_text_is_not_success():
    error = SimpleNamespace(code="subscription_sharing_usage_limit_exceeded")
    vision = Vision(
        [
            {"type": "response.output_text.delta", "delta": "Початок. "},
            {"type": "response.failed", "response": SimpleNamespace(error=error)},
        ]
    )
    with pytest.raises(RuntimeError, match="Manage usage"):
        await pipeline(vision, {}, Track())
    assert vision.stream.closed


@pytest.mark.asyncio
async def test_100_streaming_deltas_complete_without_background_jobs():
    events = [{"type": "response.output_text.delta", "delta": "Line. "} for _ in range(100)]
    events.append({"type": "response.completed"})
    vision, track = Vision(events), Track()
    await asyncio.wait_for(pipeline(vision, {}, track), timeout=2)
    assert track.snapshot()[0] == "Line. " * 100
    assert vision.stream.closed

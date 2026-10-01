import asyncio
import threading

from ucpc.config import Config
from ucpc.engine import Engine
from ucpc.history import Track


def test_new_request_cancels_old_job_without_touching_new_track(monkeypatch):
    first_started = threading.Event()
    second_started = threading.Event()
    first_cancelled = threading.Event()
    first, second = Track(), Track()

    async def generate(self, image, prompt, track):
        (first_started if track is first else second_started).set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            track.finish("Скасовано")
            if track is first:
                first_cancelled.set()
            raise

    monkeypatch.setattr(Engine, "_generate", generate)
    engine = Engine(Config(), None, lambda *args: None)
    try:
        engine.submit("dummy", "prompt", first)
        assert first_started.wait(timeout=2)
        engine.submit("dummy", "prompt", second)
        assert first_cancelled.wait(timeout=2)
        assert second_started.wait(timeout=2)
        assert first.snapshot()[1:3] == (True, "Скасовано")
        assert second.snapshot()[1] is False
        assert engine.track is second
    finally:
        engine.close()
    assert not engine.thread.is_alive()

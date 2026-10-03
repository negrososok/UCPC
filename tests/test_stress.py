import asyncio
import gc
import random
import threading
import time
import tracemalloc
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest

from ucpc.auth import PLAN_SCOPE, Auth
from ucpc.config import Config
from ucpc.engine import Engine
from ucpc.history import History, Track


@pytest.mark.stress
def test_100_000_snapshots_during_background_streaming():
    track = Track()
    errors = []

    def append():
        try:
            for _ in range(20_000):
                track.append_text("x")
        except RuntimeError as exc:
            errors.append(exc)

    worker = threading.Thread(target=append)
    worker.start()
    last_length = 0
    for _ in range(100_000):
        text, complete, error = track.snapshot()
        assert len(text) >= last_length
        last_length = len(text)
        assert not complete and not error
    worker.join(timeout=3)
    assert not worker.is_alive() and not errors
    assert len(track.snapshot()[0]) == 20_000


@pytest.mark.stress
def test_5_000_history_entries_are_retained_until_session_clear():
    history = History()
    references = []
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    for number in range(5_000):
        # Distinct strings: a folded constant would share one 8 KB allocation.
        track = Track(text=f"{number:05d}" + "x" * 8187)
        references.append(weakref.ref(track))
        history.add(track)
        history.move(-1)
        history.move(1)
    gc.collect()
    used = tracemalloc.get_traced_memory()[0] - baseline
    tracemalloc.stop()
    assert sum(ref() is not None for ref in references) == 5_000
    assert len({id(ref().text) for ref in references}) == 5_000
    assert len(history.items) == 5_000 and history.current is track
    history.index = 0
    assert history.current is references[0]()
    assert used < 60_000_000
    history.clear()
    gc.collect()
    assert sum(ref() is not None for ref in references) == 1  # local `track`
    assert not history.items and history.current is None


@pytest.mark.stress
def test_1_000_rapid_generation_replacements_leave_no_jobs(monkeypatch):
    started = 0
    cancelled = 0

    async def generate(self, image, prompt, track):
        nonlocal started, cancelled
        started += 1
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled += 1
            raise

    monkeypatch.setattr(Engine, "_generate", generate)
    engine = Engine(Config(), None, lambda *args: None)
    for _ in range(1_000):
        engine.submit("synthetic", "prompt", Track())
    engine.close()
    engine.close()
    assert not engine.thread.is_alive()
    assert started == cancelled
    with pytest.raises(RuntimeError, match="завершив"):
        engine.submit("synthetic", "prompt", Track())


@pytest.mark.stress
def test_32_simultaneous_refreshes_rotate_token_once(tmp_path, monkeypatch):
    auth = Auth(tmp_path)
    auth._save(
        {
            "client_id": "oaiapp_test",
            "subject": "test-user",
            "expires_at": 0,
            "access_token": "old",
            "refresh_token": "old-refresh",
            "scopes": [PLAN_SCOPE],
        }
    )
    requests = []

    def refresh(data):
        requests.append(data)
        time.sleep(0.03)
        return {"access_token": "new", "refresh_token": "new-refresh", "expires_in": 3600}

    monkeypatch.setattr("ucpc.auth.token_request", refresh)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: Auth(tmp_path).access_token(), range(32)))
    assert results == ["new"] * 32
    assert len(requests) == 1
    assert auth._load()["refresh_token"] == "new-refresh"


@pytest.mark.stress
def test_1_000_random_text_chunkings_preserve_order():
    rng = random.Random(94)
    words = "Український текст. Ще одне речення!\nAnd English? " * 20
    for _ in range(1_000):
        track = Track()
        index = 0
        while index < len(words):
            length = rng.randrange(1, 35)
            track.append_text(words[index : index + length])
            index += length
        track.finish()
        assert track.snapshot() == (words, True, "")


def test_finished_track_rejects_late_network_data():
    track = Track(text="original")
    track.finish("Скасовано")
    track.append_text("stale answer")
    track.set_model("stale model")
    track.finish()
    assert track.snapshot() == ("original", True, "Скасовано")
    assert track.model_name() == ""


def test_text_limit_bounds_memory():
    track = Track(max_text_chars=4)
    track.append_text("test")
    with pytest.raises(RuntimeError, match="тексту"):
        track.append_text("!")
    assert track.snapshot()[0] == "test"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "bad", True])
@pytest.mark.parametrize("field", ["request_timeout", "generation_timeout"])
def test_invalid_timeout_fails_at_load(value, field):
    with pytest.raises(ValueError):
        Config(**{field: value}).validate()

"""Thread-safe streaming responses and bounded, session-only reading history."""

import time
from dataclasses import dataclass, field
from threading import RLock


@dataclass
class Track:
    text: str = ""
    model: str = ""
    created_at: float = field(default_factory=time.time)
    complete: bool = False
    error: str = ""
    max_text_chars: int = 40_000
    scroll_y: int = 0
    scroll_x: int = 0
    _lock: RLock = field(default_factory=RLock, repr=False)

    def append_text(self, text: str) -> None:
        with self._lock:
            if self.complete:
                return
            if len(self.text) + len(text) > self.max_text_chars:
                raise RuntimeError("Відповідь перевищила ліміт тексту. Скороти системний промпт.")
            self.text += text

    def set_model(self, model: str) -> None:
        with self._lock:
            if not self.complete and isinstance(model, str) and model.strip():
                self.model = model[:200]

    def model_name(self) -> str:
        with self._lock:
            return self.model

    def finish(self, error: str = "") -> None:
        with self._lock:
            if self.complete:
                return
            self.complete, self.error = True, error

    def snapshot(self) -> tuple[str, bool, str]:
        with self._lock:
            return self.text, self.complete, self.error


class History:
    def __init__(self, limit: int = 20) -> None:
        self.limit = max(1, limit)
        self.items: list[Track] = []
        self.index = -1

    @property
    def current(self) -> Track | None:
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    def add(self, track: Track) -> None:
        self.items.append(track)
        self.items = self.items[-self.limit :]
        self.index = len(self.items) - 1

    def move(self, delta: int) -> Track | None:
        target = self.index + delta
        if not 0 <= target < len(self.items):
            return None
        self.index = target
        return self.current

    def clear(self):
        self.items.clear()
        self.index = -1

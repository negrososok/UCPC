"""Streaming text generation with latest-request cancellation."""

import asyncio
import os
import threading
from concurrent.futures import Future

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from .auth import Auth
from .config import Config
from .history import Track


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, APITimeoutError):
        return "OpenAI не відповів вчасно. Спробуй ще раз."
    if isinstance(exc, APIConnectionError):
        return "Немає з’єднання з OpenAI. Перевір інтернет."
    if isinstance(exc, APIStatusError):
        if exc.status_code == 429:
            return "Досягнуто ліміт запитів. Перевір Manage usage у меню."
        if exc.status_code in {401, 403}:
            return "Немає доступу до OpenAI. Перевір вхід і доступність моделі."
        return f"OpenAI: HTTP {exc.status_code}. Перевір модель і налаштування."
    if isinstance(exc, (RuntimeError, ValueError, FileNotFoundError)):
        return str(exc)
    return f"Помилка {type(exc).__name__}. Перевір налаштування UCPC."


async def pipeline(vision, payload: dict, track: Track) -> None:
    completed = False
    stream = await vision.responses.create(**payload)
    async with stream:
        async for event in stream:
            if event.type in {"response.output_text.delta", "response.refusal.delta"}:
                track.append_text(event.delta)
            elif event.type == "response.completed":
                track.set_model(getattr(getattr(event, "response", None), "model", ""))
                completed = True
            elif event.type in {"response.failed", "response.incomplete", "error"}:
                error = getattr(getattr(event, "response", None), "error", None)
                code = getattr(error, "code", "") or getattr(event, "code", "")
                if code in {
                    "subscription_sharing_usage_limit_exceeded",
                    "subscription_sharing_usage_unavailable",
                }:
                    raise RuntimeError("Ліміт або доступ підписки ChatGPT. Відкрий Manage usage.")
                raise RuntimeError("OpenAI не завершив відповідь: " + event.type)
    if not completed:
        raise RuntimeError("Потік OpenAI обірвався до завершення відповіді")
    if not track.snapshot()[0].strip():
        raise RuntimeError("Модель не повернула текстової відповіді")


class Engine:
    def __init__(self, config: Config, auth: Auth, on_error) -> None:
        self.config, self.auth, self.on_error = config, auth, on_error
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="UCPC-network", daemon=True)
        self.thread.start()
        self.future: Future | None = None
        self.track: Track | None = None
        self.closed = False

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.close()

    def cancel(self):
        if self.future is not None:
            self.future.cancel()
        if self.track is not None and not self.track.snapshot()[1]:
            self.track.finish("Скасовано")
        self.future = None

    def submit(self, image: str, prompt: str, track: Track):
        if self.closed:
            raise RuntimeError("UCPC уже завершив роботу")
        self.cancel()
        self.track = track
        self.future = asyncio.run_coroutine_threadsafe(
            self._generate(image, prompt, track), self.loop
        )

    async def _generate(self, image: str, prompt: str, track: Track):
        config = self.config  # mode changes apply to the next job
        try:
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if config.auth_mode == "chatgpt":
                credential = await asyncio.to_thread(self.auth.access_token)
                models = await asyncio.to_thread(self.auth.models)
                slugs = [m["slug"] for m in models]
                if not slugs:
                    raise RuntimeError("Акаунт не повернув доступних моделей")
                model = slugs[0] if config.vision_model == "auto" else config.vision_model
                if model not in slugs:
                    raise RuntimeError("Модель недоступна. Перевір каталог через --models.")
            else:
                if not api_key:
                    raise RuntimeError("Додай OPENAI_API_KEY у .env для режиму api")
                credential, model = api_key, config.api_vision_model
            track.set_model(model)
            payload = {
                "model": model,
                "instructions": prompt,
                "store": False,
                "stream": True,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": config.request_prompt},
                            {"type": "input_image", "image_url": image, "detail": "auto"},
                        ],
                    }
                ],
            }
            if config.auth_mode == "api":
                payload["max_output_tokens"] = config.max_output_tokens
            async with AsyncOpenAI(
                api_key=credential, max_retries=0, timeout=config.request_timeout
            ) as vision:
                await pipeline(vision, payload, track)
            track.finish()
        except asyncio.CancelledError:
            track.finish("Скасовано")
            raise
        except Exception as exc:  # noqa: BLE001 — failed jobs report through the UI boundary.
            while isinstance(exc, BaseExceptionGroup):
                exc = exc.exceptions[0]
            message = friendly_error(exc)
            track.finish(message)
            self.on_error(track, message)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)

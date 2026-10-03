"""Streaming text generation with latest-request cancellation."""

import asyncio
import os
import threading
import time
import uuid
from concurrent.futures import Future

import httpx
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI

from .auth import Auth
from .code_format import code_tabs
from .config import Config
from .diagnostics import record
from .history import Track
from .solver import DRAFT_INSTRUCTIONS, REVIEW_INSTRUCTIONS, VALIDATION_INSTRUCTIONS


def api_error_details(exc) -> tuple[str, str]:
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return "", ""
    body = body.get("error", body)
    if not isinstance(body, dict):
        return "", ""
    return str(body.get("code") or getattr(exc, "code", "") or ""), str(body.get("param") or "")


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return (
            "Запит перевищив час очікування. Натисни бінд відправки, щоб повторити ті самі скріни."
        )
    if isinstance(exc, APITimeoutError):
        return "OpenAI не відповів вчасно. Спробуй ще раз."
    if isinstance(exc, APIConnectionError):
        return "Немає з’єднання з OpenAI. Перевір інтернет."
    code, param = api_error_details(exc)
    if code == "token_expired":
        return "Токен входу прострочений. Увійди через ChatGPT у вкладці Акаунт та повтори запит."
    if code in {
        "subscription_sharing_usage_limit_exceeded",
        "rate_limit_exceeded",
        "insufficient_quota",
    }:
        return "Досягнуто ліміт використання. Перевір ліміти акаунта та повтори пізніше."
    if code in {"server_is_overloaded", "server_error", "internal_error", "service_unavailable"}:
        return "Сервер моделі тимчасово недоступний. Повтори ті самі скріни біндом відправки."
    if code in {"model_not_found", "subscription_sharing_usage_unavailable", "model_not_available"}:
        return "Модель зараз недоступна для цього входу. Перевір каталог та доступ акаунта."
    if isinstance(exc, APIError) and param in {"reasoning", "reasoning.effort"}:
        return "Модель не прийняла рівень аналізу. Перевір reasoning_effort у config.toml."
    if isinstance(exc, APIStatusError):
        if exc.status_code == 429:
            return "Досягнуто ліміт запитів. Перевір Manage usage у меню."
        if exc.status_code in {401, 403}:
            return "Немає доступу до OpenAI. Перевір вхід і доступність моделі."
        if exc.status_code >= 500:
            return "Тимчасовий збій сервера OpenAI. Повтори запит пізніше."
        return f"OpenAI: HTTP {exc.status_code}. Перевір модель і налаштування."
    if isinstance(exc, APIError):
        suffix = f" ({code})" if code and code.replace("_", "").isalnum() else ""
        return (
            "OpenAI перервав потік відповіді" + suffix + ". Повтори запит; деталі є в requests.log."
        )
    if isinstance(exc, (RuntimeError, ValueError, FileNotFoundError)):
        return str(exc)
    return f"Помилка {type(exc).__name__}. Перевір налаштування UCPC."


async def pipeline(vision, payload: dict, track: Track, request_id: str = "", on_first_text=None) -> None:
    completed = False
    started = time.monotonic()
    events = 0
    stream = await vision.responses.create(**payload)
    async with stream:
        track.set_phase("Модель аналізує скріншоти")
        record("stream_open", request_id=request_id)
        async for event in stream:
            events += 1
            if event.type in {"response.output_text.delta", "response.refusal.delta"}:
                if event.delta and not track.snapshot()[0]:
                    if on_first_text is not None:
                        on_first_text()
                    record(
                        "first_text",
                        request_id=request_id,
                        first_text_ms=round((time.monotonic() - started) * 1000),
                    )
                    track.set_phase("Отримуємо текст")
                track.append_text(event.delta)
            elif event.type == "response.completed":
                response = getattr(event, "response", None)
                track.set_model(getattr(response, "model", ""))
                usage = getattr(response, "usage", None)
                # Some transports return final text without preceding deltas.
                if not track.snapshot()[0].strip():
                    for item in getattr(response, "output", []) or []:
                        for part in getattr(item, "content", []) or []:
                            if getattr(part, "type", "") == "output_text":
                                track.append_text(getattr(part, "text", ""))
                            elif getattr(part, "type", "") == "refusal":
                                track.append_text(getattr(part, "refusal", ""))
                record(
                    "stream_completed",
                    request_id=request_id,
                    events=events,
                    response_id=getattr(response, "id", ""),
                    input_tokens=getattr(usage, "input_tokens", 0),
                    output_tokens=getattr(usage, "output_tokens", 0),
                    reasoning_tokens=getattr(getattr(usage, "output_tokens_details", None),
                                             "reasoning_tokens", 0),
                )
                completed = True
                break  # Completion is terminal, even if the socket stays open.
            elif event.type == "response.output_item.added":
                if (getattr(getattr(event, "item", None), "type", "") == "reasoning"
                        and not track.snapshot()[0]):
                    track.set_phase("Модель обдумує алгоритм")
                    record("reasoning_start", request_id=request_id)
            elif event.type in {"response.failed", "response.incomplete", "error"}:
                error = getattr(getattr(event, "response", None), "error", None)
                code = getattr(error, "code", "") or getattr(event, "code", "")
                response = getattr(event, "response", None)
                reason = getattr(getattr(response, "incomplete_details", None), "reason", "")
                record(
                    "stream_failed",
                    request_id=request_id,
                    events=events,
                    stage=event.type,
                    error_code=code,
                    incomplete_reason=reason,
                )
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
    TOTAL_TIMEOUT_FLOOR = 180.0
    OUTPUT_TIMEOUT = 180.0
    SETUP_TIMEOUT = 60.0

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

    def submit(self, image: str | tuple[str, ...], prompt: str, track: Track):
        if self.closed:
            raise RuntimeError("UCPC уже завершив роботу")
        self.cancel()
        self.track = track
        self.future = asyncio.run_coroutine_threadsafe(
            self._generate(image, prompt, track), self.loop
        )

    async def _generate(self, image: str | tuple[str, ...], prompt: str, track: Track):
        config = self.config  # mode changes apply to the next job
        images = (image,) if isinstance(image, str) else tuple(image)
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        record("request_start", request_id=request_id, mode=config.auth_mode,
               images=len(images), stage=track.kind)
        try:
            passes = 2 if config.verify_answer and track.kind != "verification" else 1
            deadline = (max(self.TOTAL_TIMEOUT_FLOOR, config.generation_timeout)
                        + self.OUTPUT_TIMEOUT) * passes + self.SETUP_TIMEOUT
            async with asyncio.timeout(deadline):
                await self._request(config, images, prompt, track, request_id)
            if track.kind == "answer":
                track.format_text(code_tabs)
            track.finish()
            record(
                "request_complete",
                request_id=request_id,
                duration_ms=round((time.monotonic() - started) * 1000),
                characters=len(track.snapshot()[0]),
                verification=config.verify_answer or track.kind == "verification",
            )
        except asyncio.CancelledError:
            track.finish("Скасовано")
            record(
                "request_cancelled",
                request_id=request_id,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            raise
        except Exception as exc:  # noqa: BLE001 — failed jobs report through the UI boundary.
            while isinstance(exc, BaseExceptionGroup):
                exc = exc.exceptions[0]
            message = friendly_error(exc)
            if isinstance(exc, (TimeoutError, APITimeoutError)) and track.snapshot()[0]:
                message += " Отриманий текст неповний; код потрібно отримати до кінця."
            track.finish(message)
            record(
                "request_error",
                request_id=request_id,
                error_type=type(exc).__name__,
                http_status=getattr(exc, "status_code", 0),
                duration_ms=round((time.monotonic() - started) * 1000),
                characters=len(track.snapshot()[0]),
                error_code=api_error_details(exc)[0],
                error_param=api_error_details(exc)[1],
            )
            self.on_error(track, message)

    async def _request(self, config, images, prompt, track, request_id):
        if not images:
            raise ValueError("Додай хоча б один скріншот")
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if config.auth_mode == "chatgpt":
            track.set_phase("Перевіряємо вхід")
            record("auth_start", request_id=request_id)
            credential = await asyncio.to_thread(self.auth.access_token)
            record("auth_ready", request_id=request_id)
            track.set_phase("Перевіряємо модель")
            models = await asyncio.to_thread(self.auth.models)
            record("models_ready", request_id=request_id)
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
        track.set_phase("Відправляємо скріншоти")
        record("request_open", request_id=request_id, model=model)
        payload = {
            "model": model,
            "instructions": prompt + "\n\n" + DRAFT_INSTRUCTIONS,
            "store": False,
            "stream": True,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": config.request_prompt},
                        *(
                            [
                                {
                                    "type": "input_text",
                                    "text": (
                                        f"Скріншотів: {len(images)}. Це частини одного запиту "
                                        "у порядку зйомки. Розглянь їх разом."
                                    ),
                                }
                            ]
                            if len(images) > 1
                            else []
                        ),
                        *[
                            {"type": "input_image", "image_url": image, "detail": "high"}
                            for image in images
                        ],
                    ],
                }
            ],
        }
        if config.auth_mode == "api":
            payload["max_output_tokens"] = config.max_output_tokens
        if model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
            payload["reasoning"] = {"effort": config.reasoning_effort}
        record(
            "solver_settings",
            request_id=request_id,
            reasoning_effort=config.reasoning_effort,
            verification=config.verify_answer,
            thinking_timeout_ms=round(max(self.TOTAL_TIMEOUT_FLOOR,
                                         config.generation_timeout) * 1000),
            output_grace_ms=round(self.OUTPUT_TIMEOUT * 1000),
            network_read_timeout_ms=round(config.request_timeout * 1000),
        )
        if track.kind == "verification":
            original_count = len(track.task_images)
            payload["instructions"] = prompt + "\n\n" + VALIDATION_INSTRUCTIONS
            content = [{"type": "input_text", "text": "ПОЧАТКОВА УМОВА ЗАДАЧІ:"}]
            content.extend({"type": "input_image", "image_url": image, "detail": "high"}
                           for image in images[:original_count])
            content.append({"type": "input_text", "text":
                            "НЕПЕРЕВІРЕНИЙ КАНДИДАТ РОЗВ'ЯЗАННЯ:\n" + track.candidate})
            content.append({"type": "input_text", "text":
                            "СВІЖИЙ ЕКРАН ПІСЛЯ ЗАПУСКУ/ТЕСТУВАННЯ (видимі input/output):"})
            content.extend({"type": "input_image", "image_url": image, "detail": "high"}
                           for image in images[original_count:])
            payload["input"][0]["content"] = content
            track.set_phase("Перевіряємо результат запуску")
            await self._run_pass(config, credential, payload, track, request_id)
            return
        if not config.verify_answer:
            await self._run_pass(config, credential, payload, track, request_id)
            return
        track.set_phase("Створюємо чернетку розв’язання")
        record("solver_pass", request_id=request_id, pass_name="draft")
        payload["instructions"] = prompt + "\n\n" + DRAFT_INSTRUCTIONS
        draft = Track()
        await self._run_pass(config, credential, payload, draft, request_id + ".draft")
        track.set_phase("Перевіряємо розв’язання")
        record("solver_pass", request_id=request_id, pass_name="review")
        payload["instructions"] = prompt + "\n\n" + REVIEW_INSTRUCTIONS
        # Keep the same original screenshots, adding the draft as untrusted data.
        payload["input"] = [
            payload["input"][0],
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "<unverified_draft>\n"
                            + draft.snapshot()[0]
                            + "\n</unverified_draft>"
                        ),
                    }
                ],
            },
        ]
        checked = Track()
        await self._run_pass(config, credential, payload, checked, request_id + ".review")
        track.set_model(checked.model_name() or model)
        track.append_text(checked.snapshot()[0])

    async def _run_pass(self, config, credential, payload, track, request_id):
        budget = max(self.TOTAL_TIMEOUT_FLOOR, config.generation_timeout)
        validity = budget + self.OUTPUT_TIMEOUT + 30
        if config.auth_mode == "chatgpt":
            # The previous pass can take minutes. Read the current token before
            # each new HTTP request, with enough lifetime for the whole job.
            credential = await asyncio.to_thread(self.auth.access_token, min_validity=validity)
        # Bound thinking per pass, but give late-starting output time to finish.
        # Only the first text event extends the deadline: heartbeats and later
        # deltas cannot turn a broken stream into an infinite request.
        async with asyncio.timeout(budget) as deadline:
            def started_text():
                deadline.reschedule(max(deadline.when(), asyncio.get_running_loop().time()
                                        + self.OUTPUT_TIMEOUT))

            for attempt in range(2):
                try:
                    async with AsyncOpenAI(
                        api_key=credential, max_retries=0,
                        timeout=httpx.Timeout(config.request_timeout,
                                              connect=min(15, config.request_timeout),
                                              write=min(30, config.request_timeout),
                                              pool=min(15, config.request_timeout)),
                    ) as vision:
                        await pipeline(vision, payload, track, request_id, started_text)
                    return
                except APIError as exc:
                    if (
                        config.auth_mode != "chatgpt"
                        or api_error_details(exc)[0] != "token_expired"
                        or attempt != 0
                        or track.snapshot()[0]
                    ):
                        raise
                    record("auth_refresh", request_id=request_id, error_code="token_expired")
                    credential = await asyncio.to_thread(
                        self.auth.access_token, min_validity=validity, rejected_token=credential
                    )

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)

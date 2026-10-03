"""Small rotating request journal: metadata only, never request/response content."""

import json
import logging
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

LOGGER = logging.getLogger("ucpc.requests")
LOGGER.propagate = False
LOGGER.addHandler(logging.NullHandler())
FIELDS = {
    "request_id",
    "mode",
    "model",
    "images",
    "duration_ms",
    "characters",
    "error_type",
    "http_status",
    "stage",
    "events",
    "first_text_ms",
    "response_id",
    "error_code",
    "incomplete_reason",
    "error_param",
    "pass_name",
    "reasoning_effort",
    "verification",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "thinking_timeout_ms",
    "output_grace_ms",
    "network_read_timeout_ms",
}


def configure(directory) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            directory / "requests.log", maxBytes=1_000_000, backupCount=3, encoding="utf8"
        )
    except OSError:
        return False
    for old in LOGGER.handlers[:]:
        LOGGER.removeHandler(old)
        old.close()
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    return True


def record(event: str, **fields) -> None:
    data = {"time": datetime.now(UTC).isoformat(), "event": event}
    for key, value in fields.items():
        if key not in FIELDS:
            continue
        if isinstance(value, str):
            if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,160}", value):
                continue
        elif type(value) not in (int, float, bool):
            continue
        data[key] = value
    # A logging failure must not interrupt screenshot processing.
    try:
        LOGGER.info(json.dumps(data, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — diagnostics are best effort.
        return

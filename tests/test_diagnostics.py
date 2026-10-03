import json

import pytest

from ucpc import diagnostics


@pytest.fixture
def journal(tmp_path):
    handlers, level = diagnostics.LOGGER.handlers[:], diagnostics.LOGGER.level
    diagnostics.LOGGER.handlers = []
    assert diagnostics.configure(tmp_path)
    yield tmp_path / "requests.log"
    for handler in diagnostics.LOGGER.handlers:
        handler.close()
    diagnostics.LOGGER.handlers = handlers
    diagnostics.LOGGER.setLevel(level)


def test_request_journal_only_writes_safe_metadata(journal):
    diagnostics.record(
        "request_error",
        request_id="test-request",
        images=3,
        duration_ms=420,
        error_type="APITimeoutError",
        http_status=0,
        prompt="PRIVATE_PROMPT",
        text="PRIVATE_RESPONSE",
        image="data:image/png;base64,PRIVATE_IMAGE",
        token="PRIVATE_TOKEN",
        model="invalid PRIVATE_MODEL",
    )
    text = journal.read_text(encoding="utf8")
    assert "PRIVATE" not in text
    entry = json.loads(text)
    assert entry["event"] == "request_error" and entry["images"] == 3
    assert entry["duration_ms"] == 420 and entry["error_type"] == "APITimeoutError"


def test_request_journal_rotates_with_bounded_backups(journal):
    handler = diagnostics.LOGGER.handlers[0]
    handler.maxBytes = 300
    for i in range(30):
        diagnostics.record("request_complete", request_id=f"test-{i}", characters=100)
    assert journal.exists()
    assert len(list(journal.parent.glob("requests.log.*"))) == 3


def test_unwritable_journal_does_not_prevent_application_start(tmp_path):
    file = tmp_path / "file"
    file.write_text("synthetic", encoding="utf8")
    assert diagnostics.configure(file / "directory") is False

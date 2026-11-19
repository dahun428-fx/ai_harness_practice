import json
import logging
import sys

from logging.handlers import RotatingFileHandler

from chatbot.logging_config import JsonFormatter, set_session_id, setup_logging


def test_setup_logging_writes_json_lines_to_file(tmp_path):
    log_file = tmp_path / "logs" / "chatbot.log"
    handler = setup_logging(log_file, level="INFO")
    try:
        logging.getLogger("chatbot.input_processor").info(
            "input rejected", extra={"error_code": "EMPTY"}
        )
        handler.flush()
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["error_code"] == "EMPTY"


def _log_through_setup(tmp_path, emit, **kwargs):
    log_file = tmp_path / "logs" / "chatbot.log"
    handler = setup_logging(log_file, level="INFO", **kwargs)
    try:
        emit()
        handler.flush()
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        set_session_id(None)
    return handler, [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]


def test_every_log_line_carries_current_session_id(tmp_path):
    def emit():
        set_session_id("abc123")
        logging.getLogger("chatbot.main").info("session started")
        logging.getLogger("claude_agent_sdk.x").info("sdk says hi")  # 외부 라이브러리 로그에도 붙어야 함

    _, rows = _log_through_setup(tmp_path, emit)

    assert [r["session_id"] for r in rows] == ["abc123", "abc123"]


def test_session_id_is_omitted_when_not_set(tmp_path):
    _, rows = _log_through_setup(tmp_path, lambda: logging.getLogger("chatbot.main").info("before session"))

    assert "session_id" not in rows[0]


def test_log_file_rotates_by_size(tmp_path):
    handler, _ = _log_through_setup(
        tmp_path, lambda: logging.getLogger("chatbot.main").info("x"), max_bytes=1_000, backup_count=3
    )

    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 1_000
    assert handler.backupCount == 3


def make_record(msg="input rejected", **extra):
    record = logging.LogRecord(
        name="chatbot.input_processor",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formats_record_as_single_line_json():
    line = JsonFormatter().format(make_record())

    data = json.loads(line)
    assert "\n" not in line
    assert data["level"] == "INFO"
    assert data["logger"] == "chatbot.input_processor"
    assert data["message"] == "input rejected"
    assert "time" in data


def test_includes_extra_fields():
    line = JsonFormatter().format(make_record(error_code="EMPTY", raw_length=0))

    data = json.loads(line)
    assert data["error_code"] == "EMPTY"
    assert data["raw_length"] == 0


def test_omits_standard_logrecord_attributes():
    data = json.loads(JsonFormatter().format(make_record()))

    for noisy in ("args", "msg", "pathname", "lineno", "process", "thread"):
        assert noisy not in data


def test_keeps_korean_readable():
    line = JsonFormatter().format(make_record(msg="입력 거부"))

    assert "입력 거부" in line


def test_includes_exception_traceback():
    try:
        raise ValueError("boom")
    except ValueError:
        record = make_record(msg="query failed")
        record.exc_info = sys.exc_info()

    data = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in data["exception"]
    assert "Traceback" in data["exception"]


def test_omits_exception_field_when_there_is_no_exception():
    data = json.loads(JsonFormatter().format(make_record()))

    assert "exception" not in data

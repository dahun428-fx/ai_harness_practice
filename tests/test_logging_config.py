import json
import logging

from chatbot.logging_config import JsonFormatter, setup_logging


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

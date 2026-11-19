import json
import logging
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)


def set_session_id(session_id: str | None) -> None:
    """이후 남는 모든 로그에 session_id를 붙인다. 트레이스(traces/)와 운영 로그를 잇는 열쇠."""
    _session_id.set(session_id)


class SessionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        session_id = _session_id.get()
        if session_id is not None:
            record.session_id = session_id
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        data.update({k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS})
        if record.exc_info:  # exc_info는 기본 속성이라 위에서 걸러지므로 따로 담는다
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(path, level="INFO", max_bytes=5_000_000, backup_count=5) -> logging.Handler:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)  # 로그 폴더가 없으면 만든다

    # 파일이 max_bytes를 넘으면 chatbot.log.1, .2 … 로 밀어내고 backup_count개까지만 남긴다
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SessionFilter())  # 핸들러에 붙여야 외부 라이브러리 로그에도 적용된다

    # basicConfig는 루트 로거에 핸들러가 이미 있으면 아무것도 하지 않으므로 직접 붙인다
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    return handler

import json
import logging
from pathlib import Path

_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


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


def setup_logging(path, level="INFO") -> logging.Handler:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)  # 로그 폴더가 없으면 만든다

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonFormatter())

    # basicConfig는 루트 로거에 핸들러가 이미 있으면 아무것도 하지 않으므로 직접 붙인다
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    return handler

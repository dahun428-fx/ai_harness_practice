import logging
import unicodedata
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

MAX_LENGTH = 4000


class ErrorCode(Enum):
    EMPTY = "EMPTY"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_ENCODING = "INVALID_ENCODING"
    TOO_LONG = "TOO_LONG"


@dataclass(frozen=True)
class ProcessResult:
    ok: bool
    text: str | None = None
    error: ErrorCode | None = None

    @classmethod
    def accept(cls, text: str) -> "ProcessResult":
        return cls(ok=True, text=text)

    @classmethod
    def reject(cls, error: ErrorCode) -> "ProcessResult":
        return cls(ok=False, text=None, error=error)


def process(raw) -> ProcessResult:
    try:
        decoded = _decode(raw)
        _ensure_utf8_encodable(decoded)
        text = _normalize(decoded)
        text = text.strip()
        _ensure_not_empty(text)
        _ensure_within_max_length(text)
    except _Rejected as e:
        logger.info(
            "input rejected",
            extra={
                "error_code": e.error.name,
                "input_type": type(raw).__name__,
                "raw_length": _raw_length(raw),
            },
        )
        return ProcessResult.reject(e.error)

    # 원문은 남기지 않는다. 정규화가 입력을 바꿨는지와 길이만 기록한다.
    logger.debug(
        "input accepted",
        extra={
            "input_type": type(raw).__name__,
            "raw_length": _raw_length(raw),
            "normalized_length": len(text),
            "changed": text != decoded,
        },
    )
    return ProcessResult.accept(text)


def _raw_length(raw) -> int | None:
    return len(raw) if isinstance(raw, (str, bytes)) else None


class _Rejected(Exception):
    def __init__(self, error: ErrorCode):
        self.error = error


def _decode(raw) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise _Rejected(ErrorCode.INVALID_ENCODING) from None
    raise _Rejected(ErrorCode.INVALID_TYPE)


def _ensure_utf8_encodable(text: str):
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise _Rejected(ErrorCode.INVALID_ENCODING)


def _ensure_not_empty(text: str):
    if not text:
        raise _Rejected(ErrorCode.EMPTY)


def _normalize(text: str) -> str:
    text = text.translate(_REMOVE_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    return text


def _ensure_within_max_length(text: str):
    if len(text) > MAX_LENGTH:
        raise _Rejected(ErrorCode.TOO_LONG)


_INVISIBLE_CHARS = {
    *(chr(c) for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)),  # \t, \n, \r 제외
    "\x7f",
    "\u200b",  # zero width space
    "\ufeff",  # BOM
}
_REMOVE_INVISIBLE = str.maketrans(dict.fromkeys(_INVISIBLE_CHARS))

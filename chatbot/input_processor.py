from dataclasses import dataclass
from enum import Enum
import unicodedata


class ErrorCode(Enum):
    EMPTY = "EMPTY"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_ENCODING = "INVALID_ENCODING"


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
        text = _decode(raw)
        _ensure_utf8_encodable(text)
        text = _normalize(text)
        text = text.strip()
        _ensure_not_empty(text)
    except _Rejected as e:
        return ProcessResult.reject(e.error)
    return ProcessResult.accept(text)


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

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text)

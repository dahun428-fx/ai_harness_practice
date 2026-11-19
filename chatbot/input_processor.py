from dataclasses import dataclass
from enum import Enum
import unicodedata

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
        text = _decode(raw)
        _ensure_utf8_encodable(text)
        text = _normalize(text)
        text = text.strip()
        _ensure_not_empty(text)
        _ensure_within_max_length(text)
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
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_REMOVE_INVISIBLE)
    return text


def _ensure_within_max_length(text: str):
    if len(text) > MAX_LENGTH:
        raise _Rejected(ErrorCode.TOO_LONG)


_INVISIBLE_CHARS = {
    *(chr(c) for c in range(0x20) if c not in (0x09, 0x0A)),  # \t, \n 제외
    "\x7f",
    "\u200b",  # zero width space
    "\ufeff",  # BOM
}
_REMOVE_INVISIBLE = str.maketrans(dict.fromkeys(_INVISIBLE_CHARS))

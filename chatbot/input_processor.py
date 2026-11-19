import logging
import re
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
    text = text.translate(_REMOVE_INVISIBLE)  # ESC, \r은 남김 (아래 단계에서 처리)
    text = _ANSI_ESCAPE.sub("", text)
    text = text.replace("\x1b", "")  # ANSI 시퀀스가 아닌 ESC 단독
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_SPECIAL_SPACES_TO_SPACE)
    text = unicodedata.normalize("NFC", text)
    return text


def _ensure_within_max_length(text: str):
    if len(text) > MAX_LENGTH:
        raise _Rejected(ErrorCode.TOO_LONG)


_INVISIBLE_CHARS = {
    *(chr(c) for c in range(0x20) if c not in (0x09, 0x0A, 0x0D, 0x1B)),  # \t, \n, \r, ESC 제외
    "\x7f",
    "\u200b",  # zero width space
    "\ufeff",  # BOM
}
_REMOVE_INVISIBLE = str.maketrans(dict.fromkeys(_INVISIBLE_CHARS))

# 터미널 색상·커서 제어 시퀀스(ANSI CSI): ESC [ 파라미터 중간바이트 최종바이트
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# 화면에 너비가 있는 특수 공백(유니코드 Zs: NBSP, 전각 공백 등)은 일반 공백으로 바꾼다.
# Zs 문자는 모두 BMP(U+0000~U+FFFF) 안에 있다.
_SPECIAL_SPACES_TO_SPACE = {
    c: " " for c in range(0x10000) if c != 0x20 and unicodedata.category(chr(c)) == "Zs"
}

from dataclasses import dataclass
from enum import Enum


class ErrorCode(Enum):
    EMPTY = "EMPTY"


@dataclass(frozen=True)
class ProcessResult:
    ok: bool
    text: str | None = None
    error: ErrorCode | None = None


def process(raw) -> ProcessResult:
    text = raw.strip()
    if not text:
        return ProcessResult(ok=False, text=None, error=ErrorCode.EMPTY)
    return ProcessResult(ok=True, text=text)

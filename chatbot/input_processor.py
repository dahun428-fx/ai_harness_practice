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
    # 1) 보이지 않는 문자 제거. 다른 단계보다 먼저 해야 자모 결합·CRLF·제어 시퀀스 인식을 방해하지 않는다.
    #    ESC·BEL·\r·NEL은 아래 단계에서 의미가 있으므로 여기서는 남긴다.
    text = text.translate(_REMOVE_INVISIBLE)
    text = _TAG_CHARS.sub(_keep_rgi_flag, text)
    # 2) 터미널 제어 시퀀스(OSC, CSI)를 통째로 지운 뒤, 시퀀스가 아닌 ESC·BEL을 지운다.
    text = _OSC_ESCAPE.sub("", text)
    text = _CSI_ESCAPE.sub("", text)
    text = text.replace("\x1b", "").replace("\x07", "")
    # 3) 줄바꿈 통일
    text = text.replace("\r\n", "\n").translate(_LINE_BREAKS_TO_LF)
    # 4) 특수 공백 → 일반 공백, 유니코드 정규화
    text = text.translate(_SPECIAL_SPACES_TO_SPACE)
    return unicodedata.normalize("NFC", text)


def _keep_rgi_flag(match: re.Match) -> str:
    # 정식 지역 깃발 시퀀스(🏴 + 태그 + 종료 태그)만 남기고, 나머지 태그 문자는 하나씩 지운다
    return match.group() if len(match.group()) > 1 else ""


def _ensure_within_max_length(text: str):
    if len(text) > MAX_LENGTH:
        raise _Rejected(ErrorCode.TOO_LONG)


def _chars(start: int, end: int) -> set[str]:
    return {chr(c) for c in range(start, end + 1)}


_INVISIBLE_CHARS = {
    # C0 제어문자. \t, \n, \r(줄바꿈 통일), BEL·ESC(제어 시퀀스 처리)는 제외
    *(_chars(0x00, 0x1F) - {chr(0x09), chr(0x0A), chr(0x0D), chr(0x07), chr(0x1B)}),
    chr(0x7F),
    *(_chars(0x80, 0x9F) - {chr(0x85)}),  # C1 제어문자. NEL(0x85)은 줄바꿈으로 처리
    chr(0x00AD),  # soft hyphen
    chr(0x180E),  # mongolian vowel separator
    chr(0x200B),  # zero width space
    *_chars(0x202A, 0x202E),  # 방향 embedding/override (화면 표시 순서를 뒤집는 데 악용됨)
    *_chars(0x2060, 0x2064),  # word joiner, 보이지 않는 수학 연산자
    *_chars(0x2066, 0x2069),  # 방향 isolate
    chr(0xFEFF),  # BOM
    *_chars(0xFFF9, 0xFFFB),  # interlinear annotation
    # 빈 글자처럼 보이는 문자 (메신저에서 "빈 메시지"를 보내는 데 쓰임)
    chr(0x115F),  # 한글 초성 채움
    chr(0x1160),  # 한글 중성 채움
    chr(0x3164),  # 한글 채움
    chr(0xFFA0),  # 반각 한글 채움
    chr(0x2800),  # 점자 빈칸
}
# 유지하는 보이지 않는 문자: ZWJ(U+200D)·변이 선택자(이모지 결합), ZWNJ(U+200C, 페르시아어·인도계 문자),
# LRM/RLM(U+200E, U+200F, 방향 표시만 하고 순서를 뒤집지 않음)
_REMOVE_INVISIBLE = str.maketrans(dict.fromkeys(_INVISIBLE_CHARS))

# 유니코드 태그 문자(U+E0000–E007F): 화면에 보이지 않지만 LLM은 읽을 수 있어 숨은 프롬프트 인젝션에 쓰인다.
# 정식 지역 깃발(잉글랜드·스코틀랜드·웨일스) 시퀀스만 예외로 남긴다.
_BLACK_FLAG = chr(0x1F3F4)
_CANCEL_TAG = chr(0xE007F)
_RGI_SUBDIVISION_FLAGS = ("gbeng", "gbsct", "gbwls")
_TAG_CHARS = re.compile(
    re.escape(_BLACK_FLAG)
    + "(?:"
    + "|".join("".join(chr(0xE0000 + ord(c)) for c in code) for code in _RGI_SUBDIVISION_FLAGS)
    + ")"
    + re.escape(_CANCEL_TAG)
    + f"|[{chr(0xE0000)}-{chr(0xE007F)}]"
)

# 터미널 제어 시퀀스
# OSC: ESC ] 내용 (BEL 또는 ESC \) — 하이퍼링크, 창 제목
_OSC_ESCAPE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# CSI: ESC [ 파라미터 중간바이트 최종바이트 — 색상, 커서 제어
_CSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

_LINE_BREAKS_TO_LF = str.maketrans(dict.fromkeys([chr(0x0D), chr(0x85), chr(0x2028), chr(0x2029)], "\n"))

# 화면에 너비가 있는 특수 공백(유니코드 Zs: NBSP, 전각 공백 등)은 일반 공백으로 바꾼다.
# Zs 문자는 모두 BMP(U+0000~U+FFFF) 안에 있다.
_SPECIAL_SPACES_TO_SPACE = {
    c: " " for c in range(0x10000) if c != 0x20 and unicodedata.category(chr(c)) == "Zs"
}

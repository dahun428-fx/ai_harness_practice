"""eval 케이스 JSONL 읽기/쓰기.

케이스 한 줄의 형식:
    id            고유 ID
    input         입력값 (문자열 외에 null, 숫자, 리스트 등 JSON 값도 가능 → 유형 검사용)
    input_bytes_hex   bytes 입력 (16진수 문자열)          ┐ input 계열은
    input_repeat      ["반복할 문자열", 횟수]  (긴 입력용)  ┘ 정확히 하나만
    expect        "accept" | "reject"
    normalized    accept일 때 기대하는 결과 문자열 (또는 normalized_repeat)
    error         reject일 때 기대하는 ErrorCode 이름
    tags          분류 태그. "open-question"이 붙으면 불일치를 FAIL 대신 REVIEW로 보고
    source        "synthetic" | "real" | "generated"
    note          라벨을 단 이유, 고민거리 (선택)
"""

import json
import unicodedata

INPUT_KEYS = ("input", "input_bytes_hex", "input_repeat")

# 화면에서 보이지 않거나 다른 문자와 헷갈리는 문자는 파일에 \uXXXX로 남긴다
_ALWAYS_ESCAPE = set(" 　  �")
_ESCAPE_CATEGORIES = {"Cc", "Cf", "Zs", "Zl", "Zp", "Mn", "Me", "Co", "Cn", "Cs"}


def _needs_escape(ch: str) -> bool:
    if ch == " ":
        return False
    if ch in _ALWAYS_ESCAPE:
        return True
    if 0x1100 <= ord(ch) <= 0x11FF:  # 조합형(NFD) 한글 자모
        return True
    return unicodedata.category(ch) in _ESCAPE_CATEGORIES


def _escape(ch: str) -> str:
    cp = ord(ch)
    if cp > 0xFFFF:
        cp -= 0x10000
        return "\\u%04x\\u%04x" % (0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF))
    return "\\u%04x" % cp


def dumps(case: dict) -> str:
    """한글·이모지는 그대로, 보이지 않는 문자만 이스케이프한 JSON 한 줄."""
    raw = json.dumps(case, ensure_ascii=False)
    return "".join(_escape(ch) if _needs_escape(ch) else ch for ch in raw)


def load_cases(path: str) -> list[dict]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: JSON 형식 오류: {e}") from None
            case["_where"] = f"{path}:{lineno}"
            cases.append(case)
    return cases


def validate_case(case: dict) -> list[str]:
    problems = []
    if "id" not in case:
        problems.append("id 없음")
    given = [k for k in INPUT_KEYS if k in case]
    if len(given) != 1:
        problems.append(f"input 계열 필드는 정확히 하나여야 함 (현재: {given or '없음'})")
    expect = case.get("expect")
    if expect == "accept":
        if ("normalized" in case) == ("normalized_repeat" in case):
            problems.append("accept 케이스는 normalized 또는 normalized_repeat 중 하나가 필요")
    elif expect == "reject":
        if "error" not in case:
            problems.append("reject 케이스는 error가 필요")
    else:
        problems.append(f"expect는 accept/reject 중 하나여야 함 (현재: {expect!r})")
    return problems


def build_input(case: dict):
    if "input_bytes_hex" in case:
        return bytes.fromhex(case["input_bytes_hex"])
    if "input_repeat" in case:
        text, times = case["input_repeat"]
        return text * times
    return case["input"]


def expected_text(case: dict) -> str:
    if "normalized_repeat" in case:
        text, times = case["normalized_repeat"]
        return text * times
    return case["normalized"]

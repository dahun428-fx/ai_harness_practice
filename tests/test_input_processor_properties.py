"""입력처리기 속성 기반 테스트 (hypothesis).

예시 기반 테스트가 "이 입력이면 이 결과"를 고정한다면, 여기서는 "어떤 입력이든 항상 성립해야 하는 성질"을
무작위 입력 수백 개로 확인한다. 실패하면 hypothesis가 실패를 재현하는 가장 작은 입력으로 줄여서 보여 준다.
"""

import unicodedata

from hypothesis import given, settings
from hypothesis import strategies as st

from chatbot.input_processor import MAX_LENGTH, ProcessResult, process

ESC, BEL = chr(0x1B), chr(0x07)
BLACK_FLAG, CANCEL_TAG = chr(0x1F3F4), chr(0xE007F)


def _tags(text: str) -> str:
    return "".join(chr(0xE0000 + ord(c)) for c in text)


RGI_FLAGS = [BLACK_FLAG + _tags(code) + CANCEL_TAG for code in ("gbeng", "gbsct", "gbwls")]

# 명세상 통과한 결과에 남아 있으면 안 되는 문자 (구현의 상수를 import하지 않고 명세에서 직접 옮겨 적는다)
FORBIDDEN = {
    *(chr(c) for c in range(0x00, 0x20) if chr(c) not in "\t\n"),
    chr(0x7F),
    *(chr(c) for c in range(0x80, 0xA0)),
    chr(0x00AD), chr(0x180E), chr(0x200B), chr(0xFEFF),
    *(chr(c) for c in range(0x202A, 0x202F)),
    *(chr(c) for c in range(0x2060, 0x2065)),
    *(chr(c) for c in range(0x2066, 0x206A)),
    *(chr(c) for c in range(0xFFF9, 0xFFFC)),
    chr(0x115F), chr(0x1160), chr(0x3164), chr(0xFFA0), chr(0x2800),
    chr(0x2028), chr(0x2029),
    *(chr(c) for c in range(0xE0000, 0xE0080)),
    *(chr(c) for c in range(0x10000) if chr(c) != " " and unicodedata.category(chr(c)) == "Zs"),
}

# 무작위 문자만으로는 잘 안 나오는 "까다로운 조각"을 섞어서 입력을 만든다
PIECES = [
    "안녕", "a", " ", "\t", "\n", "\r", "\r\n", chr(0x85), chr(0x2028),
    ESC, BEL, ESC + "[", ESC + "[31m", ESC + "[0m", ESC + "]8;;https://e.com" + BEL, ESC + "]0;t" + ESC + "\\",
    chr(0x200B), chr(0x200D), chr(0x200C), chr(0xFEFF), chr(0x202E), chr(0x00A0), chr(0x3000), chr(0x3164),
    chr(0x1100), chr(0x1161), chr(0x11AB),  # NFD 자모 ㄱ ㅏ ㄴ
    chr(0x0301),  # 결합 악센트
    chr(0xFE0F), chr(0x1F468), BLACK_FLAG, CANCEL_TAG, _tags("ab"), *RGI_FLAGS,
]

inputs = st.lists(st.one_of(st.text(max_size=5), st.sampled_from(PIECES)), max_size=30).map("".join)


def _strip_rgi_flags(text: str) -> str:
    for flag in RGI_FLAGS:
        text = text.replace(flag, "")
    return text


@settings(max_examples=500, deadline=None)
@given(inputs)
def test_never_raises_and_result_is_consistent(raw):
    result = process(raw)

    assert isinstance(result, ProcessResult)
    if result.ok:
        assert isinstance(result.text, str) and result.error is None
    else:
        assert result.text is None and result.error is not None


@settings(max_examples=500, deadline=None)
@given(inputs)
def test_accepted_text_satisfies_output_invariants(raw):
    result = process(raw)
    if not result.ok:
        return
    text = result.text

    assert 0 < len(text) <= MAX_LENGTH
    assert text == text.strip()
    assert unicodedata.is_normalized("NFC", text)
    leftovers = set(_strip_rgi_flags(text)) & FORBIDDEN
    assert not leftovers, sorted(hex(ord(c)) for c in leftovers)


@settings(max_examples=500, deadline=None)
@given(inputs)
def test_processing_is_idempotent(raw):
    first = process(raw)
    if not first.ok:
        return

    second = process(first.text)

    assert second.ok is True
    assert second.text == first.text


@settings(max_examples=300, deadline=None)
@given(inputs)
def test_utf8_bytes_and_str_give_same_result(raw):
    assert process(raw.encode("utf-8")) == process(raw)

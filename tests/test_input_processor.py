import pytest
import unicodedata
from chatbot.input_processor import ErrorCode, process


def test_plain_text_is_accepted_as_is():
    result = process("안녕")

    assert result.ok is True
    assert result.text == "안녕"


@pytest.mark.parametrize(
    "raw",
    [
        "  안녕",  # 앞 공백
        "안녕  ",  # 뒤 공백
        "\t안녕\n",  # 탭, 줄바꿈
        "\u3000안녕",  # 전각 공백 (한글 키보드에서 실제로 들어옴)
    ],
)
def test_leading_and_trailing_whitespace_is_stripped(raw):
    result = process(raw)

    assert result.ok is True
    assert result.text == "안녕"


def test_inner_whitespace_is_preserved():
    result = process("def f():\n    return 1")

    assert result.text == "def f():\n    return 1"


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "\n\t", "\u3000"],
    ids=["empty", "spaces", "newline-tab", "fullwidth-space"],
)
def test_blank_input_is_rejected_as_empty(raw):
    result = process(raw)

    assert result.ok is False
    assert result.error is ErrorCode.EMPTY
    assert result.text is None


@pytest.mark.parametrize(
    "raw",
    [None, 123, ["안녕"], {"text": "안녕"}],
    ids=["none", "int", "list", "dict"],
)
def test_non_text_input_is_rejected_as_invalid_type(raw):
    result = process(raw)

    assert result.ok is False
    assert result.error is ErrorCode.INVALID_TYPE
    assert result.text is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("안녕".encode("utf-8"), "안녕"),
        ("  안녕  ".encode("utf-8"), "안녕"),
    ],
    ids=["utf8", "utf8-with-whitespace"],
)
def test_utf8_bytes_are_decoded(raw, expected):
    result = process(raw)

    assert result.ok is True
    assert result.text == expected


@pytest.mark.parametrize(
    "raw",
    [
        "안녕".encode("cp949"),  # 한국어 Windows 기본 인코딩
        b"\xff\xfe\xfd",  # UTF-8로 불가능한 바이트
        "안".encode("utf-8")[:2],  # 잘린 UTF-8 (3바이트 중 2바이트만)
    ],
    ids=["cp949", "invalid-bytes", "truncated-utf8"],
)
def test_non_utf8_bytes_are_rejected_as_invalid_encoding(raw):
    result = process(raw)

    assert result.ok is False
    assert result.error is ErrorCode.INVALID_ENCODING
    assert result.text is None


@pytest.mark.parametrize(
    "raw",
    [
        "\udcec\udc95\udc88",  # '안'이 surrogateescape된 형태
        "안녕\udcff",  # 정상 텍스트 뒤에 섞인 경우
        "\ud83d",  # 짝 없는 high surrogate (이모지가 잘린 흔적)
    ],
    ids=["surrogate-escaped-korean", "trailing-surrogate", "lone-high-surrogate"],
)
def test_str_with_lone_surrogates_is_rejected_as_invalid_encoding(raw):
    result = process(raw)

    assert result.ok is False
    assert result.error is ErrorCode.INVALID_ENCODING
    assert result.text is None


def test_emoji_is_accepted():
    result = process("안녕😊")

    assert result.ok is True
    assert result.text == "안녕😊"


def test_decomposed_korean_is_normalized_to_nfc():
    nfd = unicodedata.normalize("NFD", "안녕")
    assert nfd != "안녕"  # 전제 조건: 입력이 정말 NFD인지

    result = process(nfd)

    assert result.ok is True
    assert result.text == "안녕"


@pytest.mark.parametrize(
    "raw",
    ["①", "Ａ", "ﬁ", "㎏"],
    ids=["circled-digit", "fullwidth-latin", "ligature", "unit-symbol"],
)
def test_compatibility_characters_are_preserved(raw):
    result = process(raw)

    assert result.ok is True
    assert result.text == raw

import logging
import unicodedata

import pytest

from chatbot.input_processor import MAX_LENGTH, ErrorCode, process

LOGGER_NAME = "chatbot.input_processor"


def test_rejection_log_for_non_text_input_has_no_length(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    process(42)

    record = caplog.records[0]
    assert record.error_code == "INVALID_TYPE"
    assert record.input_type == "int"
    assert record.raw_length is None


def test_rejection_is_logged_at_info_with_error_code(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    process("")

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.INFO
    assert record.error_code == "EMPTY"


def test_rejection_log_includes_input_metadata(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    process("a" * (MAX_LENGTH + 1))

    record = caplog.records[0]
    assert record.error_code == "TOO_LONG"
    assert record.input_type == "str"
    assert record.raw_length == MAX_LENGTH + 1


def test_accepted_input_is_not_logged_at_info(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    process("안녕")

    assert caplog.records == []


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


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("첫줄\r\n둘째줄", "첫줄\n둘째줄"),
        ("첫줄\r둘째줄", "첫줄\n둘째줄"),
        ("첫줄\r\n\r\n셋째줄", "첫줄\n\n셋째줄"),
        ("a\r\nb\rc\nd", "a\nb\nc\nd"),
    ],
    ids=["crlf", "cr", "blank-line-crlf", "mixed"],
)
def test_line_endings_are_normalized_to_lf(raw, expected):
    result = process(raw)

    assert result.ok is True
    assert result.text == expected


@pytest.mark.parametrize(
    "raw",
    ["안\x00녕", "안\x07녕", "안\x1b녕", "안\x7f녕"],
    ids=["null", "bell", "escape", "delete"],
)
def test_control_characters_are_removed(raw):
    result = process(raw)

    assert result.ok is True
    assert result.text == "안녕"


@pytest.mark.parametrize(
    "raw",
    ["안\u200b녕", "\ufeff안녕", "\u200b안녕\u200b"],
    ids=["zwsp-inside", "bom-leading", "zwsp-around"],
)
def test_zero_width_characters_are_removed(raw):
    result = process(raw)

    assert result.ok is True
    assert result.text == "안녕"


def test_newline_and_tab_are_preserved():
    result = process("a\tb\nc")

    assert result.text == "a\tb\nc"


@pytest.mark.parametrize(
    "raw",
    [
        "\U0001f468\u200d\U0001f469\u200d\U0001f467",  # 👨‍👩‍👧 (ZWJ 시퀀스)
        "\u2764\ufe0f",  # ❤️ (variation selector)
        "\U0001f44d\U0001f3fd",  # 👍🏽 (피부색 수정자)
    ],
    ids=["zwj-family", "variation-selector", "skin-tone"],
)
def test_emoji_sequences_are_preserved(raw):
    result = process(raw)

    assert result.ok is True
    assert result.text == raw


@pytest.mark.parametrize(
    "raw",
    ["\u200b", "\x00\x00", "\ufeff \u200b"],
    ids=["zwsp-only", "nulls-only", "bom-space-zwsp"],
)
def test_only_invisible_characters_is_rejected_as_empty(raw):
    result = process(raw)

    assert result.ok is False
    assert result.error is ErrorCode.EMPTY


def test_max_length_matches_spec():
    assert MAX_LENGTH == 4000


def test_input_at_max_length_is_accepted():
    result = process("a" * MAX_LENGTH)

    assert result.ok is True


def test_input_over_max_length_is_rejected_as_too_long():
    result = process("a" * (MAX_LENGTH + 1))

    assert result.ok is False
    assert result.error is ErrorCode.TOO_LONG
    assert result.text is None


def test_length_counts_characters_not_bytes():
    result = process("가" * MAX_LENGTH)  # UTF-8로는 12000바이트

    assert result.ok is True


@pytest.mark.parametrize(
    "raw",
    [
        "   " + "a" * MAX_LENGTH + "   ",
        "\u200b" * 10 + "a" * MAX_LENGTH,
        unicodedata.normalize("NFD", "가" * MAX_LENGTH),  # 정규화 전 8000자
        "a" * (MAX_LENGTH - 2)
        + "\r\n"
        + "b",  # 정규화 후 a 3998 + \n + b = 4000자 (정규화 전 4001자)
    ],
    ids=["surrounding-spaces", "zero-width-padding", "nfd-korean", "crlf"],
)
def test_length_is_measured_after_normalization(raw):
    result = process(raw)

    assert result.ok is True


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("\u110b\u200b\u1161\u11ab", "안"),  # 최소 재현: ㅇ + ZWSP + ㅏ + ㄴ
        ("\u110b\u1161\u11ab\u1102\u1167\x00\u11bc", "안녕"),  # 받침 앞에 끼인 경우
        (
            "\u110b\x1b\u1161\u11ab\u1102\u1167\x01\u11bc\ud558\uc138\uc694",
            "안녕하세요",
        ),  # eval rand-42-042
    ],
    ids=["minimal", "before-final-consonant", "eval-rand-42-042"],
)
def test_invisible_characters_between_decomposed_jamo_do_not_block_nfc(raw, expected):
    result = process(raw)

    assert result.ok is True
    assert result.text == expected


@pytest.mark.parametrize(
    "raw",
    [
        "첫줄\r\x1b\n둘째줄",
        "첫줄\r\u200b\n둘째줄",
        "첫줄\r\x00\x07\n둘째줄",
    ],
    ids=["escape", "zwsp", "multiple"],
)
def test_invisible_characters_inside_crlf_do_not_split_line_break(raw):
    result = process(raw)

    assert result.ok is True
    assert result.text == "첫줄\n둘째줄"

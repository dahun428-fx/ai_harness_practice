import pytest
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

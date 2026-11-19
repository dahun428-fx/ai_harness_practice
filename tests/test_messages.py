import pytest

from chatbot.input_processor import ErrorCode
from chatbot.messages import REJECTION_MESSAGES


@pytest.mark.parametrize("code", list(ErrorCode), ids=lambda c: c.name)
def test_every_error_code_has_a_user_message(code):
    message = REJECTION_MESSAGES[code]

    assert isinstance(message, str)
    assert message.strip()


def test_too_long_message_mentions_the_limit():
    from chatbot.input_processor import MAX_LENGTH

    assert f"{MAX_LENGTH:,}" in REJECTION_MESSAGES[ErrorCode.TOO_LONG]

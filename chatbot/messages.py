from chatbot.input_processor import MAX_LENGTH, ErrorCode

REJECTION_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.EMPTY: "메시지를 입력해 주세요.",
    ErrorCode.TOO_LONG: f"메시지가 너무 길어요. {MAX_LENGTH:,}자 이하로 나눠서 보내 주세요.",
    ErrorCode.INVALID_ENCODING: "올바르지 않은 인코딩입니다.",
    ErrorCode.INVALID_TYPE: "올바르지 않은 입력 형식입니다.",
}

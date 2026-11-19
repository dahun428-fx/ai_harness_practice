import logging
import os
import sys
from pathlib import Path

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
)

from chatbot.input_processor import process
from chatbot.logging_config import setup_logging
from chatbot.messages import REJECTION_MESSAGES

LOG_FILE = Path(__file__).parent / "logs" / "chatbot.log"
EXIT_COMMANDS = ("exit", "quit")

logger = logging.getLogger("chatbot.main")


def read_user_input() -> str | None:
    """입력을 받는다. Ctrl+C / Ctrl+D(EOF)면 None."""
    try:
        return input("You: ")
    except (EOFError, KeyboardInterrupt):
        return None


async def ask(client: ClaudeSDKClient, text: str) -> None:
    logger.debug("query sent", extra={"text_length": len(text)})
    await client.query(text)
    print("Claude: ", end="", flush=True)
    async for msg in client.receive_response():
        logger.debug("sdk message received", extra={"message_type": type(msg).__name__})
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(block.text, end="", flush=True)
        elif isinstance(msg, ResultMessage):
            logger.info(
                "response completed",
                extra={
                    "duration_ms": msg.duration_ms,
                    "cost_usd": msg.total_cost_usd,
                    "is_error": msg.is_error,
                },
            )
    print()


async def main() -> None:
    options = ClaudeAgentOptions(
        system_prompt="너는 친절한 한국어 비서야",
        tools=[],
        setting_sources=[],  # CLAUDE.md 등 프로젝트 설정을 읽지 않음
    )

    logger.info("session started")
    async with ClaudeSDKClient(options=options) as client:
        while True:
            raw = read_user_input()
            if raw is None:
                print()
                break

            result = process(raw)
            if not result.ok:
                print(REJECTION_MESSAGES[result.error])
                continue
            if result.text.lower() in EXIT_COMMANDS:
                break

            try:
                await ask(client, result.text)
            except ClaudeSDKError:
                logger.exception("query failed")
                print("\n응답을 받지 못했어요. 잠시 후 다시 시도해 주세요.")
    logger.info("session ended")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")  # Git Bash 등에서 stdin이 cp949로 잡히는 문제
    setup_logging(LOG_FILE, level="INFO")
    # LOG_LEVEL=DEBUG는 우리 코드(chatbot.*)에만 적용한다.
    # 루트까지 DEBUG로 내리면 SDK 등 외부 라이브러리의 DEBUG 로그(대화 내용이 들어갈 수 있음)까지 파일에 남는다.
    logging.getLogger("chatbot").setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    try:
        anyio.run(main)
    except KeyboardInterrupt:  # 답변이 출력되는 도중에 Ctrl+C를 누른 경우
        print()

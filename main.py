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
from chatbot.logging_config import set_session_id, setup_logging
from chatbot.messages import REJECTION_MESSAGES
from chatbot.tracing import TraceWriter, app_version, new_session_id, prompt_version

ROOT = Path(__file__).parent
LOG_FILE = ROOT / "logs" / "chatbot.log"
TRACE_DIR = ROOT / "traces"
EXIT_COMMANDS = ("exit", "quit")
SYSTEM_PROMPT = "너는 친절한 한국어 비서야"

logger = logging.getLogger("chatbot.main")


def read_user_input() -> str | None:
    """입력을 받는다. Ctrl+C / Ctrl+D(EOF)면 None."""
    try:
        return input("You: ")
    except (EOFError, KeyboardInterrupt):
        return None


async def ask(client: ClaudeSDKClient, text: str) -> dict:
    """질문을 보내고 답을 화면에 출력한다. 트레이스에 남길 응답 정보를 돌려준다."""
    logger.debug("query sent", extra={"text_length": len(text)})
    await client.query(text)
    print("Claude: ", end="", flush=True)
    parts, model, outcome = [], None, {}
    async for msg in client.receive_response():
        logger.debug("sdk message received", extra={"message_type": type(msg).__name__})
        if isinstance(msg, AssistantMessage):
            model = msg.model
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(block.text, end="", flush=True)
                    parts.append(block.text)
        elif isinstance(msg, ResultMessage):
            logger.info(
                "response completed",
                extra={"duration_ms": msg.duration_ms, "cost_usd": msg.total_cost_usd, "is_error": msg.is_error},
            )
            outcome = {
                "sdk_session_id": msg.session_id,
                "stop_reason": msg.stop_reason,
                "is_error": msg.is_error,
                "num_turns": msg.num_turns,
                "duration_ms": msg.duration_ms,
                "duration_api_ms": msg.duration_api_ms,
                "cost_usd": msg.total_cost_usd,
                "usage": msg.usage,
            }
    print()
    return {"text": "".join(parts), "model": model, **outcome}


async def main(tracer: TraceWriter, session_id: str) -> None:
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        tools=[],
        setting_sources=[],  # CLAUDE.md 등 프로젝트 설정을 읽지 않음
    )
    version = {**app_version(ROOT), "prompt_version": prompt_version(SYSTEM_PROMPT)}

    logger.info("session started", extra=version)
    tracer.write({"type": "session_start", "session_id": session_id, **version, "system_prompt": SYSTEM_PROMPT})

    turn = 0
    async with ClaudeSDKClient(options=options) as client:
        while True:
            raw = read_user_input()
            if raw is None:
                print()
                break

            result = process(raw)
            if result.ok and result.text.lower() in EXIT_COMMANDS:
                break

            turn += 1
            record = {
                "type": "turn",
                "session_id": session_id,
                "turn": turn,
                "input": {
                    "raw": raw,
                    "normalized": result.text,
                    "ok": result.ok,
                    "error": result.error.name if result.error else None,
                },
            }
            if not result.ok:
                print(REJECTION_MESSAGES[result.error])
                tracer.write(record)
                continue

            try:
                record["output"] = await ask(client, result.text)
            except ClaudeSDKError as e:
                logger.exception("query failed")
                record["error"] = f"{type(e).__name__}: {e}"
                print("\n응답을 받지 못했어요. 잠시 후 다시 시도해 주세요.")
            tracer.write(record)

    logger.info("session ended", extra={"turns": turn})
    tracer.write({"type": "session_end", "session_id": session_id, "turns": turn})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")  # Git Bash 등에서 stdin이 cp949로 잡히는 문제
    setup_logging(LOG_FILE, level="INFO")
    # LOG_LEVEL=DEBUG는 우리 코드(chatbot.*)에만 적용한다.
    # 루트까지 DEBUG로 내리면 SDK 등 외부 라이브러리의 DEBUG 로그(대화 내용이 들어갈 수 있음)까지 파일에 남는다.
    logging.getLogger("chatbot").setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

    # 트레이스(원문 포함)는 기본으로 켜고, TRACE=0 이면 끈다. 보관 기간이 지난 파일은 시작할 때 지운다.
    tracer = TraceWriter(TRACE_DIR, enabled=os.environ.get("TRACE", "1") != "0")
    tracer.cleanup(retention_days=int(os.environ.get("TRACE_RETENTION_DAYS", "30")))

    session_id = new_session_id()
    set_session_id(session_id)
    try:
        anyio.run(main, tracer, session_id)
    except KeyboardInterrupt:  # 답변이 출력되는 도중에 Ctrl+C를 누른 경우
        print()
    except Exception:
        logger.exception("unexpected error")  # 예상하지 못한 오류로 죽어도 원인은 로그에 남긴다
        raise

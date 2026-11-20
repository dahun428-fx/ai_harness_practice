"""터미널 챗봇 진입점.

입출력과 흐름 제어만 맡는다. 실제 규칙은 각 모듈에 있다.
- 입력 검증/정규화: chatbot/input_processor.py
- 프롬프트 구성:    chatbot/prompt_builder.py (+ prompts/chat.yaml)
- 운영 로그:        chatbot/logging_config.py (원문 금지)
- 트레이스:         chatbot/tracing.py (원문 포함, traces/)

`if __name__ == "__main__"` 가드 덕분에 테스트가 이 모듈을 import해도 봇이 시작되지 않는다.
"""

# logging: 운영 로그. 원문(사용자 입력, Claude 응답)은 절대 남기지 않는다.
import logging

# os: 환경 변수(LOG_LEVEL, TRACE, TRACE_RETENTION_DAYS)를 읽는다
import os

# sys: 표준 입출력 인코딩을 UTF-8로 바꾼다
import sys

# Path: 이 파일 위치를 기준으로 logs/, traces/, prompts/ 경로를 만든다
from pathlib import Path

# anyio: async 함수(main)를 실행하는 이벤트 루프. Claude Agent SDK가 async API라서 필요하다.
import anyio

# Claude Agent SDK: 로컬 Claude Code 로그인으로 Claude와 대화한다(API 키 불필요)
from claude_agent_sdk import (
    AssistantMessage,  # Claude가 보낸 응답 메시지 (본문 블록들을 담는다)
    ClaudeAgentOptions,  # 세션 설정: 시스템 프롬프트, 도구, 설정 파일 사용 여부 등
    ClaudeSDKClient,  # 대화를 유지하는 클라이언트. 대화 기록은 이 세션이 들고 있다.
    ClaudeSDKError,  # SDK 오류의 공통 부모. 네트워크/CLI 문제 등을 한 번에 잡는다.
    ResultMessage,  # 한 번의 질의가 끝났다는 메시지 (소요 시간, 비용, 토큰 사용량)
    TextBlock,  # 응답 본문 중 텍스트 조각
)

# 입력처리기: 원문을 검증·정규화한다. 예외를 던지지 않고 ProcessResult를 돌려준다.
from chatbot.input_processor import process

# 로깅 설정: 파일 핸들러 연결, 모든 로그 줄에 session_id 붙이기
from chatbot.logging_config import set_session_id, setup_logging

# 거부 사유(ErrorCode)별 사용자 안내 문구
from chatbot.messages import REJECTION_MESSAGES

# 프롬프트 구성기: 프롬프트 파일을 읽고(load_prompt_spec) 최종 시스템 지시를 만든다(render_system)
from chatbot.prompt_builder import PromptSpec, TemplateError, load_prompt_spec, render_system

# 트레이스: 턴마다 원문을 기록. 버전 정보(git 커밋, 프롬프트 해시)도 함께 남긴다.
from chatbot.tracing import TraceWriter, app_version, new_session_id, prompt_version

# 이 파일이 있는 폴더(프로젝트 루트). 실행 위치와 무관하게 경로가 같도록 기준으로 삼는다.
ROOT = Path(__file__).parent
# 운영 로그 파일 (JSON Lines, 5MB × 5개 회전)
LOG_FILE = ROOT / "logs" / "chatbot.log"
# 트레이스 폴더 (UTC 날짜별 jsonl 파일)
TRACE_DIR = ROOT / "traces"
# 시스템 지시를 담은 프롬프트 파일. 프롬프트를 바꿀 때는 코드가 아니라 이 파일을 고친다.
PROMPT_FILE = ROOT / "prompts" / "chat.yaml"
# 이 단어를 입력하면(정규화 후, 대소문자 무시) 대화를 끝낸다
EXIT_COMMANDS = ("exit", "quit")

# 이 모듈 전용 로거. "chatbot." 아래에 있어야 LOG_LEVEL 설정이 적용된다.
logger = logging.getLogger("chatbot.main")


def read_user_input() -> str | None:
    """입력을 받는다. Ctrl+C / Ctrl+D(EOF)면 None."""
    try:
        # 프롬프트 "You: "를 보여 주고 한 줄을 읽는다 (끝의 줄바꿈은 빠진 채로 돌아온다)
        return input("You: ")
    except (EOFError, KeyboardInterrupt):
        # EOFError: Ctrl+D(Windows는 Ctrl+Z) 또는 파이프 입력이 끝남 / KeyboardInterrupt: Ctrl+C
        # 둘 다 "그만하겠다"는 뜻이라 예외 대신 None으로 알린다
        return None


async def ask(client: ClaudeSDKClient, text: str) -> dict:
    """질문을 보내고 답을 화면에 출력한다. 트레이스에 남길 응답 정보를 돌려준다."""
    # 운영 로그에는 길이만 남긴다 (원문 금지 규칙)
    logger.debug("query sent", extra={"text_length": len(text)})
    # 질문을 보낸다. 대화 기록은 SDK 세션이 들고 있으므로 이번 입력만 보내면 된다.
    await client.query(text)
    # 응답 앞머리. flush=True로 즉시 화면에 내보낸다(버퍼에 쌓이지 않게).
    print("Claude: ", end="", flush=True)
    # parts: 응답 텍스트 조각들 / model: 응답한 모델 이름 / outcome: 끝났을 때의 통계
    parts, model, outcome = [], None, {}
    # 응답은 여러 메시지로 나눠 도착한다(스트리밍). 이번 질의가 끝날 때까지 하나씩 받는다.
    async for msg in client.receive_response():
        # 메시지 종류만 기록한다 (내용은 기록하지 않음)
        logger.debug("sdk message received", extra={"message_type": type(msg).__name__})
        # Claude의 응답 본문
        if isinstance(msg, AssistantMessage):
            # 어떤 모델이 답했는지 (트레이스에 남겨 모델 변경에 따른 드리프트를 추적한다)
            model = msg.model
            # 본문은 여러 블록으로 구성된다. 텍스트 블록만 화면에 출력하고 모은다.
            for block in msg.content:
                if isinstance(block, TextBlock):
                    # 도착하는 대로 바로 출력해 사용자가 기다리지 않게 한다
                    print(block.text, end="", flush=True)
                    # 트레이스에 남길 전체 응답을 만들기 위해 모은다
                    parts.append(block.text)
        # 이번 질의가 끝났다는 신호. 소요 시간, 비용, 토큰 사용량이 들어 있다.
        elif isinstance(msg, ResultMessage):
            # 운영 로그에는 메타데이터만 남긴다
            logger.info(
                "response completed",
                extra={"duration_ms": msg.duration_ms, "cost_usd": msg.total_cost_usd, "is_error": msg.is_error},
            )
            # 트레이스용 응답 통계
            outcome = {
                "sdk_session_id": msg.session_id,  # SDK 쪽 세션 ID (우리 session_id와 별개)
                "stop_reason": msg.stop_reason,  # 응답이 끝난 이유 (정상 종료, 길이 제한 등)
                "is_error": msg.is_error,  # SDK가 오류로 판단했는지
                "num_turns": msg.num_turns,  # SDK 내부 턴 수
                "duration_ms": msg.duration_ms,  # 전체 소요 시간
                "duration_api_ms": msg.duration_api_ms,  # 그중 API 호출 시간
                "cost_usd": msg.total_cost_usd,  # 비용(달러)
                "usage": msg.usage,  # 입력/출력 토큰 수
            }
    # 응답이 끝나면 줄을 바꾼다
    print()
    # 응답 원문 + 모델 + 통계를 하나의 dict로 돌려준다 (트레이스의 output 필드)
    return {"text": "".join(parts), "model": model, **outcome}


async def main(tracer: TraceWriter, session_id: str, spec: PromptSpec, system_prompt: str) -> None:
    """대화 루프. 프롬프트는 __main__에서 읽고 렌더링한 결과를 인자로 받는다(테스트에서 가짜를 넣을 수 있게)."""
    # SDK 세션 설정
    options = ClaudeAgentOptions(
        # 인자로 받은 렌더링 결과를 그대로 쓴다. 여기서 다시 렌더링하면 모델에 보낸 프롬프트와
        # 트레이스에 기록한 프롬프트가 서로 다른 계산 결과가 되어, 변수가 생기는 순간 기록이 실제와 어긋날 수 있다.
        system_prompt=system_prompt,
        # 도구 없음: 순수 대화만 한다 (파일 읽기, 명령 실행 등 불가)
        tools=[],
        # CLAUDE.md 등 프로젝트 설정을 읽지 않음 (개발용 지시가 챗봇에 섞이지 않게)
        setting_sources=[],
    )
    # 이 세션이 어떤 코드와 어떤 프롬프트로 돌았는지. 드리프트를 추적할 때 이 값으로 구간을 나눈다.
    # prompt_version은 렌더링된 시스템 지시의 해시라, 템플릿이나 예시가 바뀌면 값이 바뀐다.
    version = {**app_version(ROOT), "prompt_id": spec.id, "prompt_version": prompt_version(system_prompt)}

    # 운영 로그에는 버전 정보만, 트레이스에는 시스템 프롬프트 원문까지 남긴다
    logger.info("session started", extra=version)
    tracer.write({"type": "session_start", "session_id": session_id, **version, "system_prompt": system_prompt})

    # 이 세션에서 기록한 턴 수 (exit/quit은 세지 않는다)
    turn = 0
    # async with: 블록을 벗어날 때(정상 종료든 예외든) SDK 연결을 정리한다
    async with ClaudeSDKClient(options=options) as client:
        while True:
            # 한 줄 입력. None이면 Ctrl+C / Ctrl+D
            raw = read_user_input()
            if raw is None:
                # 입력 줄 뒤에 줄바꿈을 넣어 터미널 프롬프트가 같은 줄에 붙지 않게 한다
                print()
                break

            # 입력처리기로 검증·정규화. 예외를 던지지 않는다.
            result = process(raw)
            # 종료 명령은 정규화된 텍스트로 판정한다 ("  EXIT\u200b" 같은 입력도 종료로 인식)
            if result.ok and result.text.lower() in EXIT_COMMANDS:
                break

            # 기록할 턴 번호 (1부터)
            turn += 1
            # 이번 턴의 트레이스 레코드. 원문(raw)과 정규화 결과를 함께 남긴다.
            record = {
                "type": "turn",
                "session_id": session_id,
                "turn": turn,
                "input": {
                    "raw": raw,  # 사용자가 실제로 입력한 그대로 (깨진 입력 포함)
                    "normalized": result.text,  # 입력처리기 결과 (거부되면 None)
                    "ok": result.ok,  # 통과 여부
                    "error": result.error.name if result.error else None,  # 거부 사유 (EMPTY, TOO_LONG 등)
                },
            }
            # 거부된 입력: 안내 문구만 보여 주고 Claude에는 보내지 않는다
            if not result.ok:
                print(REJECTION_MESSAGES[result.error])
                tracer.write(record)
                continue

            try:
                # Claude에게 묻고 응답 정보를 레코드에 담는다
                record["output"] = await ask(client, result.text)
            except ClaudeSDKError as e:
                # SDK 오류: traceback은 운영 로그에, 오류 요약은 트레이스에 남기고 대화는 계속한다
                logger.exception("query failed")
                record["error"] = f"{type(e).__name__}: {e}"
                print("\n응답을 받지 못했어요. 잠시 후 다시 시도해 주세요.")
            # 성공이든 실패든 턴 기록은 남긴다
            tracer.write(record)

    # 세션 종료 기록 (몇 턴을 기록했는지)
    logger.info("session ended", extra={"turns": turn})
    tracer.write({"type": "session_end", "session_id": session_id, "turns": turn})


if __name__ == "__main__":
    # Windows 콘솔 기본 인코딩(cp949)은 이모지 등을 출력하지 못하므로 UTF-8로 바꾼다
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")  # Git Bash 등에서 stdin이 cp949로 잡히는 문제
    # 운영 로그를 파일에 연결한다. 이후의 모든 로그(오류 포함)가 파일에 남으려면 가장 먼저 해야 한다.
    setup_logging(LOG_FILE, level="INFO")
    # LOG_LEVEL=DEBUG는 우리 코드(chatbot.*)에만 적용한다.
    # 루트까지 DEBUG로 내리면 SDK 등 외부 라이브러리의 DEBUG 로그(대화 내용이 들어갈 수 있음)까지 파일에 남는다.
    logging.getLogger("chatbot").setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

    # 프롬프트 파일은 SDK 연결 전에 읽는다. 잘못된 파일이면 대화가 시작되기 전에 실패한다(fail fast).
    # 렌더링도 여기서 한 번만 한다. 이 결과를 SDK 설정과 트레이스 기록이 함께 쓴다(단일 출처).
    try:
        spec = load_prompt_spec(PROMPT_FILE)
        system_prompt = render_system(spec, {})  # 변수가 생기면 이 dict로 값을 넘긴다
    except TemplateError:
        # 아래쪽 try는 anyio.run만 감싸므로, 프롬프트 오류는 여기서 따로 로그에 남기고 다시 던진다
        logger.exception("invalid prompt file", extra={"path": str(PROMPT_FILE)})
        raise

    # 트레이스(원문 포함)는 기본으로 켜고, TRACE=0 이면 끈다. 보관 기간이 지난 파일은 시작할 때 지운다.
    tracer = TraceWriter(TRACE_DIR, enabled=os.environ.get("TRACE", "1") != "0")
    tracer.cleanup(retention_days=int(os.environ.get("TRACE_RETENTION_DAYS", "30")))

    # 이번 실행의 세션 ID. 운영 로그의 모든 줄과 트레이스에 붙어 두 기록을 잇는 열쇠가 된다.
    session_id = new_session_id()
    set_session_id(session_id)
    try:
        # async main을 이벤트 루프에서 실행한다
        anyio.run(main, tracer, session_id, spec, system_prompt)
    except KeyboardInterrupt:  # 답변이 출력되는 도중에 Ctrl+C를 누른 경우
        print()
    except Exception:
        logger.exception("unexpected error")  # 예상하지 못한 오류로 죽어도 원인은 로그에 남긴다
        raise

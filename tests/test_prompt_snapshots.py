import difflib
import json
import os
from pathlib import Path

import pytest

from chatbot.prompt_builder import (
    Document,
    Example,
    Message,
    PromptSpec,
    build_prompt,
    load_prompt_spec,
    render_system,
)

SNAPSHOTS = Path(__file__).parent / "snapshots"
PROMPTS = Path(__file__).parent.parent / "prompts"


def format_prompt(prompt):
    """Prompt를 사람이 읽고 diff로 비교할 수 있는 텍스트로 바꾼다."""
    parts = [f"=== system ===\n{prompt.system}"]
    parts += [f"=== {m.role} ===\n{m.content}" for m in prompt.messages]
    parts.append(f"=== metadata ===\n{json.dumps(prompt.metadata(), sort_keys=True)}")
    return "\n\n".join(parts) + "\n"


def assert_snapshot(name, text):
    path = SNAPSHOTS / f"{name}.txt"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    if not path.exists():
        pytest.fail(f"snapshot missing: {path.name} (create with UPDATE_SNAPSHOTS=1, then review the file)")
    # read_text는 CRLF를 LF로 읽으므로, 체크아웃 줄바꿈이 달라도 내용이 같으면 같다고 본다
    expected = path.read_text(encoding="utf-8")
    if text != expected:
        # pytest의 기본 비교는 긴 문자열을 잘라서 보여 준다. git diff처럼 바뀐 줄만 -/+로 보여 준다.
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True), text.splitlines(keepends=True), "snapshot", "actual"
            )
        )
        pytest.fail(f"{path.name} changed (UPDATE_SNAPSHOTS=1 if intended, then review):\n{diff}")


def test_chat_prompt_snapshot():
    # 실제 운영 프롬프트. chat.yaml을 고치면 모델이 받을 최종 텍스트가 PR diff로 보인다.
    spec = load_prompt_spec(PROMPTS / "chat.yaml")

    prompt = build_prompt(system=render_system(spec, {}), user_input="안녕")

    assert_snapshot("chat_basic", format_prompt(prompt))


FULL_SPEC = PromptSpec(
    id="full",
    description="모든 구성 요소",
    variables=("name",),
    system="너는 ${name}의 비서야\n규칙: 짧게 답해",
    examples=(Example("2+2는?", "4"),),
)


def test_full_prompt_snapshot():
    # 변수, 예시, 대화 기록, 이스케이프가 필요한 문서, 예산 초과로 버려지는 턴을 한 번에 담는다
    prompt = build_prompt(
        system=render_system(FULL_SPEC, {"name": "테스터"}),
        user_input="요약해 줘",
        history=[
            Message("user", "옛 질문"),
            Message("assistant", "옛 답"),
            Message("user", "최근 질문"),
            Message("assistant", "최근 답"),
        ],
        documents=[Document('a "b".txt', "<script>x & y</script>")],
        max_chars=258,
    )

    assert prompt.dropped_turns == 1  # 예산이 의도대로 걸렸는지. 아니면 스냅샷이 다른 상황을 고정한다
    assert_snapshot("full", format_prompt(prompt))

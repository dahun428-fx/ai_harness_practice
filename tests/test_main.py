from functools import partial

import anyio
import pytest
from claude_agent_sdk import AssistantMessage, ClaudeSDKError, ResultMessage, TextBlock

# main.py를 import하는 것 자체가 진입점의 문법·import 오류를 잡는 검사다.
# __main__ 가드 덕분에 import만으로는 봇이 시작되지 않는다.
import main
from chatbot.prompt_builder import PromptSpec, spec_version
from chatbot.tracing import prompt_version

SPEC = PromptSpec(id="t", description="d", variables=(), system="SYSTEM", examples=())
SECRET = "hunter2-secret"


class ListTracer:
    """TraceWriter 대신 레코드를 리스트에 모은다."""

    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


class FakeClient:
    """ClaudeSDKClient 대신 쓴다. script의 각 항목은 응답 문자열 또는 query에서 던질 예외."""

    def __init__(self, options, script):
        self.options = options
        self.script = list(script)
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, text):
        self.queries.append(text)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        self._reply = step

    async def receive_response(self):
        yield AssistantMessage(content=[TextBlock(self._reply)], model="fake-model")
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="sdk"
        )


def run(inputs, script=()):
    """inputs를 차례로 입력하고 끝나면 EOF(None). 기록된 트레이스와 가짜 클라이언트를 돌려준다."""
    tracer, clients, it = ListTracer(), [], iter(inputs)

    def factory(options):
        clients.append(FakeClient(options, script))
        return clients[-1]

    # anyio.run은 키워드 인자를 받지 않으므로 partial로 묶는다
    anyio.run(
        partial(main.main, tracer, "sid", SPEC, "SYSTEM", client_factory=factory, read_input=lambda: next(it, None))
    )
    return tracer.records, clients[0]


def turns(records):
    return [r for r in records if r["type"] == "turn"]


def test_session_options_use_rendered_system_prompt_and_no_tools():
    _, client = run([])

    assert client.options.system_prompt == "SYSTEM"
    assert client.options.tools == []
    assert client.options.setting_sources == []


def test_only_current_turn_is_sent_to_sdk():
    _, client = run(["안녕", "  두번째\r\n"], ["a1", "a2"])

    assert client.queries == ["안녕", "두번째"]  # 정규화된 이번 턴만. 기록은 SDK 세션이 가진다


def test_turn_trace_has_prompt_metadata_and_output():
    records, _ = run(["안녕"], ["반가워"])

    (turn,) = turns(records)
    assert turn["prompt"] == {"message_count": 1, "dropped_turns": 0, "document_count": 0, "total_chars": 8}
    assert turn["output"]["text"] == "반가워"
    assert turn["output"]["model"] == "fake-model"


def test_rejected_input_is_not_sent_and_has_no_prompt():
    records, client = run(["   ", "안녕"], ["a"])

    assert client.queries == ["안녕"]
    rejected = turns(records)[0]
    assert rejected["input"]["error"] == "EMPTY"
    assert "prompt" not in rejected
    assert "output" not in rejected


def test_sdk_error_is_traced_and_conversation_continues():
    records, client = run(["q1", "q2"], [ClaudeSDKError("boom"), "a2"])

    first, second = turns(records)
    assert "boom" in first["error"]
    assert "output" not in first
    assert second["output"]["text"] == "a2"
    assert client.queries == ["q1", "q2"]  # 오류 뒤에도 다음 질문은 SDK로 간다


@pytest.mark.parametrize("command", ["exit", "  EXIT  "])
def test_exit_ends_session_without_recording_a_turn(command):
    records, client = run(["q", command, "never"], ["a"])

    assert client.queries == ["q"]
    assert records[-1] == {"type": "session_end", "session_id": "sid", "turns": 1}


def test_session_start_records_versions():
    records, _ = run([])

    start = records[0]
    assert start["type"] == "session_start"
    assert start["prompt_id"] == "t"
    # "둘이 다르다"가 아니라 각각 정답과 비교한다. 그래야 두 값이 뒤바뀐 버그도 잡힌다.
    assert start["prompt_version"] == spec_version(SPEC)
    assert start["system_hash"] == prompt_version("SYSTEM")


def test_conversation_content_is_never_logged(caplog):
    caplog.set_level("DEBUG")

    run([f"질문 {SECRET}"], [f"응답 {SECRET}"])

    assert SECRET not in caplog.text
    for record in caplog.records:
        assert SECRET not in str(record.__dict__)

    run([f"질문 {SECRET}"], [f"응답 {SECRET}"])

    assert SECRET not in caplog.text
    for record in caplog.records:
        assert SECRET not in str(record.__dict__)

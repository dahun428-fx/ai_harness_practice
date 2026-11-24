"""LLM 호출기 계약 테스트 (명세: docs/llm-caller.md "검증").

모든 호출기 구현이 같은 테스트를 통과해야 한다. impl fixture가 IMPLS의 구현마다 테스트를 한 번씩 돌린다.
새 구현(C2 Agent SDK, C5 Messages API)은 IMPLS에 한 줄 추가하면 같은 계약을 검사받는다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import anyio
import pytest

from chatbot.callers.fake import FakeCaller
from chatbot.prompt_builder import Message, Prompt


@dataclass(frozen=True)
class Impl:
    """계약 테스트가 구현마다 알아야 하는 것."""

    # (responses, model=None) -> 호출기. responses: 호출 한 번마다 공급자가 낼 이벤트 목록
    make: Callable
    # 호출기 -> 공급자가 실제로 받은 것들 (호출 순서대로)
    sent: Callable
    # Prompt -> 공급자가 받아야 할 것 (구현마다 다르다: 가짜는 Prompt 통째로, Agent SDK는 마지막 메시지 텍스트)
    expected: Callable


IMPLS = {
    "fake": Impl(
        make=lambda responses, model=None: FakeCaller(responses, model=model),
        sent=lambda caller: caller.prompts,
        expected=lambda prompt: prompt,
    ),
}


# 이 fixture를 인자로 받는 테스트는 IMPLS의 구현마다 한 번씩 실행된다 (테스트 이름에 [fake] 등이 붙는다)
@pytest.fixture(params=list(IMPLS.values()), ids=list(IMPLS))
def impl(request):
    return request.param


PROMPT = Prompt(system="SYSTEM", messages=(Message("user", "안녕"),))


async def consume(caller, prompt):
    """호출기를 열고 스트림을 끝까지 받아 (받은 이벤트들, RawResponse)를 돌려준다."""
    async with caller:
        call = caller.stream(prompt)
        received = [event async for event in call]
        return received, call.response


def test_events_are_passed_through_unchanged(impl):
    # 문자열 대신 object()를 쓴다: 값이 같은 게 아니라 "같은 객체"인지(is) 확인하려고
    e1, e2 = object(), object()
    caller = impl.make([[e1, e2]])

    received, response = anyio.run(partial(consume, caller, PROMPT))

    assert len(received) == 2
    assert received[0] is e1
    assert received[1] is e2
    assert response.events == (e1, e2)


def test_provider_receives_expected_content(impl):
    caller = impl.make([[object()]])

    anyio.run(partial(consume, caller, PROMPT))

    # 공급자가 받은 것 = 이 구현이 받아야 할 것 (호출 1번).
    # is가 아니라 ==: Agent SDK가 받는 건 문자열이라 "같은 객체"가 모든 구현의 계약은 아니다.
    assert impl.sent(caller) == [impl.expected(PROMPT)]
    # 호출 뒤에도 프롬프트가 그대로인가
    assert PROMPT == Prompt(system="SYSTEM", messages=(Message("user", "안녕"),))


def test_stream_requires_open_session(impl):
    caller = impl.make([[object()], [object()]])

    # 열기 전
    with pytest.raises(RuntimeError, match="async with"):
        caller.stream(PROMPT)

    # 연 뒤 닫은 다음에도 안 된다
    async def open_and_close():
        async with caller:
            pass

    anyio.run(open_and_close)
    with pytest.raises(RuntimeError, match="async with"):
        caller.stream(PROMPT)


def test_response_requires_fully_consumed_stream(impl):
    caller = impl.make([[object(), object()]])

    async def scenario():
        async with caller:
            call = caller.stream(PROMPT)
            # 하나도 안 받았을 때
            with pytest.raises(RuntimeError, match="not consumed"):
                _ = call.response
            # 절반만 받았을 때: 이 상태의 response는 "일부"라서 완성된 응답처럼 보이면 안 된다
            stream = aiter(call)
            await anext(stream)
            with pytest.raises(RuntimeError, match="not consumed"):
                _ = call.response

    anyio.run(scenario)


def test_stream_can_be_iterated_only_once(impl):
    caller = impl.make([[object()]])

    async def scenario():
        async with caller:
            call = caller.stream(PROMPT)
            _ = [e async for e in call]
            # 두 번째 소비자(예: 출력처리기와 트레이스가 각자 순회)는 빈 응답이 아니라 오류를 받아야 한다
            with pytest.raises(RuntimeError, match="already"):
                _ = [e async for e in call]

    anyio.run(scenario)


def test_overlapping_calls_are_rejected(impl):
    first_event, second_event = object(), object()
    caller = impl.make([[first_event], [second_event]])

    async def scenario():
        async with caller:
            first = caller.stream(PROMPT)
            # 첫 스트림을 다 받기 전에 다음 호출 → 거부
            with pytest.raises(RuntimeError, match="in progress"):
                caller.stream(PROMPT)
            assert [e async for e in first] == [first_event]
            # 다 받은 뒤에는 다음 호출이 되고, 다음 응답이 나온다
            second = caller.stream(PROMPT)
            assert [e async for e in second] == [second_event]

    anyio.run(scenario)


def test_session_can_reopen_after_unfinished_stream(impl):
    second_event = object()
    caller = impl.make([[object(), object()], [second_event]])

    async def scenario():
        async with caller:
            call = caller.stream(PROMPT)
            # 하나만 받고 세션을 나간다 (Ctrl+C나 오류로 출력이 중간에 끊긴 상황)
            await anext(aiter(call))
        # 다시 열면 이전의 미완료 스트림 때문에 막히면 안 된다
        async with caller:
            again = caller.stream(PROMPT)
            assert [e async for e in again] == [second_event]

    anyio.run(scenario)


def test_response_carries_provider_model_and_metrics(impl):
    caller = impl.make([[object()]], model="test-model")

    _, response = anyio.run(partial(consume, caller, PROMPT))

    # provider는 구현마다 다르므로 값이 아니라 "호출기가 밝힌 이름과 같은가"를 본다
    assert response.provider == caller.provider
    assert response.requested_model == "test-model"
    assert response.metrics.attempts == 1


def test_model_defaults_to_none(impl):
    # None = 공급자 기본 모델을 쓴다 (명세)
    caller = impl.make([[object()]])

    _, response = anyio.run(partial(consume, caller, PROMPT))

    assert response.requested_model is None

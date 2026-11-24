from functools import partial

import anyio
import pytest

from chatbot.callers.fake import FakeCaller
from chatbot.prompt_builder import Message, Prompt

PROMPT = Prompt(system="SYSTEM", messages=(Message("user", "안녕"),))


async def consume(caller, prompt):
    """호출기를 열고 스트림을 끝까지 받아 (받은 이벤트들, RawResponse)를 돌려준다."""
    async with caller:
        call = caller.stream(prompt)
        received = [event async for event in call]
        return received, call.response


def test_events_are_passed_through_unchanged():
    # 문자열 대신 object()를 쓴다: 값이 같은 게 아니라 "같은 객체"인지(is) 확인하려고
    e1, e2 = object(), object()
    caller = FakeCaller(responses=[[e1, e2]])

    received, response = anyio.run(partial(consume, caller, PROMPT))

    assert len(received) == 2
    assert received[0] is e1
    assert received[1] is e2
    assert response.events == (e1, e2)


def test_prompt_is_passed_to_provider_unchanged():
    caller = FakeCaller(responses=[[object()]])

    anyio.run(partial(consume, caller, PROMPT))

    # 가짜 공급자가 받은 프롬프트 목록. 호출 1번 = 1개
    assert len(caller.prompts) == 1
    # 다시 만든 비슷한 객체가 아니라, 넘긴 그 객체를 받았는가
    assert caller.prompts[0] is PROMPT
    # 호출 뒤에도 프롬프트가 그대로인가 (frozen이지만 내용 비교로 한 번 더)
    assert PROMPT == Prompt(system="SYSTEM", messages=(Message("user", "안녕"),))


def test_stream_requires_open_session():
    caller = FakeCaller(responses=[[object()], [object()]])

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


def test_response_requires_fully_consumed_stream():
    caller = FakeCaller(responses=[[object(), object()]])

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


def test_overlapping_calls_are_rejected():
    first_event, second_event = object(), object()
    caller = FakeCaller(responses=[[first_event], [second_event]])

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

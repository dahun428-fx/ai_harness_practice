"""LLM 호출기의 공통 모양 (명세: docs/llm-caller.md).

프롬프트 구성기가 만든 Prompt를 받아 모델을 호출하고, 원시 응답을 손대지 않고 다음 단계로 넘긴다.
응답 파싱·검증·출력은 이 모듈의 일이 아니다(출력처리기의 몫).
"""

from dataclasses import dataclass


# 호출 한 번의 측정값(지연, 사용량, 비용). 내용은 담지 않는다. 지연·사용량·비용 필드는 C3에서 추가한다.
@dataclass(frozen=True)
class CallMetrics:
    # 시도 횟수. 재시도가 없으면 1 (재시도는 C4)
    attempts: int


# 호출 한 번의 원시 응답. 만든 뒤 바뀌지 않는 "값"이라 frozen이다.
@dataclass(frozen=True)
class RawResponse:
    # 어느 공급자가 응답했는가 ("fake", "claude-agent-sdk" ...)
    provider: str
    # 요청한 모델. None = 공급자 기본값. 실제로 응답한 모델은 측정값(C3)에 따로 남긴다.
    requested_model: str | None
    # 공급자가 보낸 이벤트 객체 원본을 도착 순서대로 담는다. 변환·복사하지 않는다(파싱은 출력처리기 몫).
    # list가 아니라 tuple이라 다음 단계가 실수로 고칠 수 없다.
    events: tuple
    # 이 호출의 측정값
    metrics: CallMetrics


class BaseCaller:
    """모든 호출기 구현의 부모. 바깥(main)은 이 모양만 안다.

    하위 클래스는 공급자 사정만 채운다: _open(prompt) = 시도 1회, 원시 이벤트를 내보내는 async generator.
    재시도·타임아웃·측정·로깅은 나중에 이 클래스 한 곳에 모인다(측정 지점을 하나로).
    """

    # 하위 클래스가 반드시 정한다 ("fake", "claude-agent-sdk" ...)
    provider: str

    def __init__(self, model=None):
        # 요청할 모델. 코드가 아니라 설정에서 정한다(C5). None = 공급자 기본값.
        self.model = model
        # async with 안에 있는가. 세션을 열지 않은 호출은 프로그래밍 오류로 즉시 막는다.
        self._opened = False
        # 진행 중인 호출의 스트림. 한 세션 안의 호출은 순차적이어야 한다(대화 순서가 섞이지 않게).
        self._current = None

    # async with caller: 로 세션을 연다. 실제로 열 것(SDK 연결 등)은 C2에서 하위 클래스가 채운다.
    async def __aenter__(self):
        self._opened = True
        return self

    # async with 블록을 나갈 때 세션을 닫는다. False를 돌려줘 블록 안의 예외를 삼키지 않는다.
    # 다 받지 못한 스트림(Ctrl+C, 오류)을 잊어서, 다시 열었을 때 "진행 중"으로 막히지 않게 한다.
    async def __aexit__(self, *exc):
        self._opened = False
        self._current = None
        return False

    def stream(self, prompt):
        """Prompt를 보내고 원시 이벤트 스트림을 돌려준다. 끝까지 소비한 뒤 .response로 RawResponse를 얻는다."""
        if not self._opened:
            raise RuntimeError("stream() must be called inside async with")
        # 이전 스트림을 다 받기 전에 새로 부르면, 두 응답의 이벤트가 섞이거나 앞의 것이 버려진다
        if self._current is not None and not self._current.done:
            raise RuntimeError("previous call still in progress: consume its stream first")
        # 스트림이 끝나면 RawResponse에 누가·무엇을 요청했는지 담을 수 있게 함께 넘긴다
        self._current = CallStream(self._open(prompt), provider=self.provider, model=self.model)
        return self._current

    async def _open(self, prompt):
        # 하위 클래스가 반드시 채워야 한다
        raise NotImplementedError
        yield  # 이 줄 덕분에 이 메서드도 async generator가 된다(하위 클래스와 같은 모양)


class CallStream:
    """원시 이벤트를 하나씩 내보내면서 모아 두고, 끝나면 RawResponse를 만든다.

    async generator 하나로 끝내지 않고 클래스로 둔 이유:
    async generator는 다 소비한 뒤 결과를 꺼낼 방법이 없다. 이벤트는 흘려보내면서(스트리밍 UX 유지)
    다 받은 뒤 전체(RawResponse)도 얻어야 해서 상태를 담을 객체가 필요하다.
    """

    def __init__(self, source, provider, model):
        # source: 공급자 구현의 _open(prompt)가 만든 async generator
        self._source = source
        # RawResponse에 그대로 옮겨 담을 호출 정보
        self._provider = provider
        self._model = model
        # 지금까지 받은 이벤트
        self._events = []
        # 스트림이 끝나야 채워진다
        self._response = None
        # 공급자가 이벤트를 다 보냈는가. BaseCaller가 겹친 호출을 막을 때도 읽는다(그래서 공개 속성).
        self.done = False
        # 순회를 시작했는가. 스트림은 한 번만 소비할 수 있다(두 번째 소비자가 빈 응답을 받지 않게).
        # done이 아니라 시작 여부로 막는다: 절반만 받은 스트림을 처음부터 다시 도는 것도 같은 실수다.
        self._started = False

    async def __aiter__(self):
        if self._started:
            raise RuntimeError("stream already consumed: use .response for the full result")
        self._started = True
        # async def + yield = async generator → __anext__를 따로 만들지 않아도 async for가 동작한다
        async for event in self._source:
            # 원본 객체를 그대로 모으고 그대로 내보낸다(복사·변환하지 않음)
            self._events.append(event)
            yield event
        # 여기까지 왔다 = 공급자가 이벤트를 다 보냈다
        self._response = RawResponse(
            provider=self._provider,
            requested_model=self._model,
            events=tuple(self._events),
            metrics=CallMetrics(attempts=1),  # 재시도가 생기면(C4) 실제 횟수로 바뀐다
        )
        self.done = True

    @property
    def response(self):
        """스트림을 끝까지 소비한 뒤의 RawResponse."""
        # 일부만 받은 상태를 완성된 응답처럼 돌려주면, 다음 단계가 "짧은 답"으로 오해한다
        if not self.done:
            raise RuntimeError("stream not consumed to the end yet")
        return self._response
